param(
    [string]$EventsDirectory = "C:\Users\pgoff\.claude\glassbox\events",
    [string[]]$ProgressJson = @(),
    [string[]]$ArtifactPath = @(),
    [string]$WindowTitle = "GLASS BOX - PROCESS MATRIX",
    [int]$PollMilliseconds = 500,
    [int]$ReplayEvents = 120,
    [int]$ReplayArtifacts = 20,
    [int]$UiBatchSize = 80,
    [int]$MaxQueueItems = 5000,
    [int]$MaxFeedCharacters = 1500000,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot

function Resolve-ViewerPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $Workspace $Path))
}

$ResolvedEventsDirectory = Resolve-ViewerPath $EventsDirectory
$ResolvedProgressJson = @(
    foreach ($Path in $ProgressJson) {
        $Resolved = Resolve-ViewerPath $Path
        if ($Resolved) { $Resolved }
    }
)
$ResolvedArtifactPath = @(
    foreach ($Path in $ArtifactPath) {
        $Resolved = Resolve-ViewerPath $Path
        if ($Resolved) { $Resolved }
    }
)

$EngineSource = @'
using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace GlassboxProcessMatrix
{
    public sealed class UiItem
    {
        public string TimestampUtc = "";
        public string Lane = "";
        public string Source = "";
        public string RunId = "";
        public string Kind = "";
        public string Status = "";
        public string Stage = "";
        public string Summary = "";
        public string Detail = "";
        public string Command = "";
        public string Input = "";
        public string Output = "";
        public string Artifact = "";
        public string Failure = "";
        public string Metadata = "";
        public string Origin = "";
        public int Current = -1;
        public int Total = -1;

        public string MatrixKey
        {
            get
            {
                return (Lane ?? "") + "\u001f" + (Source ?? "") + "\u001f" + (RunId ?? "");
            }
        }
    }

    internal sealed class PendingPath
    {
        public string Path;
        public string Adapter;

        public PendingPath(string path, string adapter)
        {
            Path = path;
            Adapter = adapter;
        }
    }

    public sealed class Engine : IDisposable
    {
        private readonly string _eventsDirectory;
        private readonly string[] _progressPaths;
        private readonly string[] _artifactPaths;
        private readonly int _pollMilliseconds;
        private readonly int _replayEvents;
        private readonly int _replayArtifacts;
        private readonly int _maxQueueItems;
        private readonly ConcurrentQueue<UiItem> _output = new ConcurrentQueue<UiItem>();
        private readonly ConcurrentQueue<PendingPath> _pending = new ConcurrentQueue<PendingPath>();
        private readonly Dictionary<string, string> _fingerprints =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        private readonly List<FileSystemWatcher> _watchers = new List<FileSystemWatcher>();
        private readonly JavaScriptSerializer _json = new JavaScriptSerializer();
        private Thread _thread;
        private volatile bool _stopping;
        private int _queuedItems;
        private int _droppedItems;

        private static readonly Regex SensitiveKey = new Regex(
            @"(?i)(password|passwd|pwd|secret|client.?secret|token|authorization|api.?key|cookie|set.?cookie|cf.?clearance|credential|session.?id|private.?key)",
            RegexOptions.Compiled);
        private static readonly Regex SensitiveArtifactName = new Regex(
            @"(?i)(^\.env(?:\.|$)|id_[rd]sa|\.p(?:em|12|fx)$|\.key$|password|passwd|secret|token|api.?key|cookie|cf.?clearance|cf.?session|credential|private.?key)",
            RegexOptions.Compiled);
        private static readonly Regex SensitiveFlag = new Regex(
            @"(?i)(--?(?:api[-_]?key|access[-_]?token|refresh[-_]?token|token|password|passwd|pwd|secret|client[-_]?secret|authorization|cookie|cf[-_]?clearance)\s*(?:=|\s)\s*)(?:""[^""]*""|'[^']*'|[^\s]+)",
            RegexOptions.Compiled);
        private static readonly Regex SensitiveAssignment = new Regex(
            @"(?i)((?:api[-_]?key|access[-_]?token|refresh[-_]?token|token|password|passwd|pwd|secret|client[-_]?secret|authorization|cookie|set[-_]?cookie|cf[-_]?clearance)\s*[:=]\s*)(?:""[^""]*""|'[^']*'|[^,;\s}\]]+)",
            RegexOptions.Compiled);
        private static readonly Regex BearerValue = new Regex(
            @"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
            RegexOptions.Compiled);
        private static readonly Regex SensitiveQuery = new Regex(
            @"(?i)([?&](?:access_token|refresh_token|token|api_key|apikey|key|sig|signature|x-amz-credential|x-amz-signature)=)[^&\s]+",
            RegexOptions.Compiled);
        private static readonly Regex Fraction = new Regex(
            @"(?<!\d)(?<current>\d{1,12})\s*/\s*(?<total>\d{1,12})(?!\d)",
            RegexOptions.Compiled);

        public Engine(
            string eventsDirectory,
            string[] progressPaths,
            string[] artifactPaths,
            int pollMilliseconds,
            int replayEvents,
            int replayArtifacts,
            int maxQueueItems)
        {
            _eventsDirectory = eventsDirectory ?? "";
            _progressPaths = progressPaths ?? new string[0];
            _artifactPaths = artifactPaths ?? new string[0];
            _pollMilliseconds = Math.Max(200, Math.Min(10000, pollMilliseconds));
            _replayEvents = Math.Max(0, Math.Min(5000, replayEvents));
            _replayArtifacts = Math.Max(0, Math.Min(1000, replayArtifacts));
            _maxQueueItems = Math.Max(100, Math.Min(50000, maxQueueItems));
            _json.MaxJsonLength = Int32.MaxValue;
            _json.RecursionLimit = 64;
        }

        public int DroppedItems { get { return Volatile.Read(ref _droppedItems); } }
        public int QueuedItems { get { return Math.Max(0, Volatile.Read(ref _queuedItems)); } }

        public void Start()
        {
            if (_thread != null) return;
            _thread = new Thread(WorkerLoop);
            _thread.IsBackground = true;
            _thread.Name = "GlassboxProcessMatrixReader";
            _thread.Start();
        }

        public bool TryTake(out UiItem item)
        {
            if (_output.TryDequeue(out item))
            {
                Interlocked.Decrement(ref _queuedItems);
                return true;
            }
            return false;
        }

        public void Stop()
        {
            _stopping = true;
            foreach (FileSystemWatcher watcher in _watchers)
            {
                try { watcher.EnableRaisingEvents = false; }
                catch { }
            }
            if (_thread != null && _thread.IsAlive)
            {
                _thread.Join(2500);
            }
        }

        public void Dispose()
        {
            Stop();
            foreach (FileSystemWatcher watcher in _watchers)
            {
                try { watcher.Dispose(); }
                catch { }
            }
            _watchers.Clear();
        }

        private void WorkerLoop()
        {
            try
            {
                ConfigureWatchers();
                QueueInitialReplay();
                while (!_stopping)
                {
                    DrainPending(250);
                    PollProgressAdapters();
                    Thread.Sleep(_pollMilliseconds);
                }
            }
            catch (Exception ex)
            {
                Enqueue(SystemItem("viewer.worker.failed", "failed", ex.Message));
            }
        }

        private void ConfigureWatchers()
        {
            if (Directory.Exists(_eventsDirectory))
            {
                AddWatcher(_eventsDirectory, "*.json", false, "event");
            }
            else
            {
                Enqueue(SystemItem(
                    "viewer.events.missing",
                    "waiting",
                    "Events directory is not present yet: " + SanitizeText(_eventsDirectory)));
            }

            foreach (string artifactPath in _artifactPaths)
            {
                try
                {
                    if (Directory.Exists(artifactPath))
                    {
                        AddWatcher(artifactPath, "*", true, "artifact");
                    }
                    else
                    {
                        string parent = Path.GetDirectoryName(artifactPath);
                        string filter = Path.GetFileName(artifactPath);
                        if (!String.IsNullOrWhiteSpace(parent) && Directory.Exists(parent))
                        {
                            AddWatcher(parent, filter, false, "artifact");
                        }
                    }
                }
                catch (Exception ex)
                {
                    Enqueue(SystemItem("viewer.artifact.watch.failed", "failed", ex.Message));
                }
            }
        }

        private void AddWatcher(string directory, string filter, bool recursive, string adapter)
        {
            FileSystemWatcher watcher = new FileSystemWatcher(directory, filter);
            watcher.IncludeSubdirectories = recursive;
            watcher.NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite |
                NotifyFilters.Size | NotifyFilters.CreationTime;
            watcher.Created += delegate(object sender, FileSystemEventArgs args)
            {
                _pending.Enqueue(new PendingPath(args.FullPath, adapter));
            };
            watcher.Changed += delegate(object sender, FileSystemEventArgs args)
            {
                _pending.Enqueue(new PendingPath(args.FullPath, adapter));
            };
            watcher.Renamed += delegate(object sender, RenamedEventArgs args)
            {
                _pending.Enqueue(new PendingPath(args.FullPath, adapter));
            };
            watcher.Error += delegate(object sender, ErrorEventArgs args)
            {
                Enqueue(SystemItem("viewer.watch.error", "failed", args.GetException().Message));
            };
            watcher.EnableRaisingEvents = true;
            _watchers.Add(watcher);
        }

        private void QueueInitialReplay()
        {
            if (Directory.Exists(_eventsDirectory) && _replayEvents > 0)
            {
                try
                {
                    FileInfo[] events = new DirectoryInfo(_eventsDirectory)
                        .GetFiles("*.json", SearchOption.TopDirectoryOnly)
                        .OrderByDescending(f => f.LastWriteTimeUtc)
                        .Take(_replayEvents)
                        .OrderBy(f => f.LastWriteTimeUtc)
                        .ToArray();
                    foreach (FileInfo file in events)
                    {
                        _pending.Enqueue(new PendingPath(file.FullName, "event"));
                    }
                }
                catch (Exception ex)
                {
                    Enqueue(SystemItem("viewer.event.replay.failed", "failed", ex.Message));
                }
            }

            foreach (string artifactPath in _artifactPaths)
            {
                try
                {
                    if (File.Exists(artifactPath))
                    {
                        _pending.Enqueue(new PendingPath(artifactPath, "artifact"));
                    }
                    else if (Directory.Exists(artifactPath) && _replayArtifacts > 0)
                    {
                        FileInfo[] files = new DirectoryInfo(artifactPath)
                            .GetFiles("*", SearchOption.AllDirectories)
                            .OrderByDescending(f => f.LastWriteTimeUtc)
                            .Take(_replayArtifacts)
                            .OrderBy(f => f.LastWriteTimeUtc)
                            .ToArray();
                        foreach (FileInfo file in files)
                        {
                            _pending.Enqueue(new PendingPath(file.FullName, "artifact"));
                        }
                    }
                }
                catch (Exception ex)
                {
                    Enqueue(SystemItem("viewer.artifact.replay.failed", "failed", ex.Message));
                }
            }
        }

        private void DrainPending(int maximum)
        {
            PendingPath pending;
            int count = 0;
            while (count < maximum && _pending.TryDequeue(out pending))
            {
                count += 1;
                if (_stopping) return;
                if (pending == null || String.IsNullOrWhiteSpace(pending.Path)) continue;
                try
                {
                    if (String.Equals(pending.Adapter, "event", StringComparison.OrdinalIgnoreCase))
                    {
                        ProcessJsonPath(pending.Path, "event");
                    }
                    else
                    {
                        ProcessArtifactPath(pending.Path);
                    }
                }
                catch (Exception ex)
                {
                    Enqueue(SystemItem(
                        "viewer.read.failed",
                        "failed",
                        SanitizeText(pending.Path) + ": " + ex.Message));
                }
            }
        }

        private void PollProgressAdapters()
        {
            foreach (string path in _progressPaths)
            {
                if (_stopping) return;
                try
                {
                    if (File.Exists(path))
                    {
                        ProcessJsonPath(path, "progress");
                    }
                }
                catch (Exception ex)
                {
                    Enqueue(SystemItem(
                        "viewer.progress.read.failed",
                        "failed",
                        SanitizeText(path) + ": " + ex.Message));
                }
            }
        }

        private void ProcessJsonPath(string path, string adapter)
        {
            string text;
            if (!TryReadStableText(path, out text)) return;
            string fingerprint = Sha256(text);
            string key = adapter + "|" + path;
            string previous;
            if (_fingerprints.TryGetValue(key, out previous) &&
                String.Equals(previous, fingerprint, StringComparison.Ordinal))
            {
                return;
            }

            object parsedObject;
            try
            {
                parsedObject = _json.DeserializeObject(text);
            }
            catch
            {
                return;
            }
            Dictionary<string, object> root = parsedObject as Dictionary<string, object>;
            if (root == null) return;

            UiItem item = adapter == "progress"
                ? BuildProgressItem(root, path)
                : BuildEventItem(root, path);
            _fingerprints[key] = fingerprint;
            Enqueue(item);
        }

        private void ProcessArtifactPath(string path)
        {
            if (!File.Exists(path)) return;
            FileInfo file = new FileInfo(path);
            if (file.Length < 0) return;
            string fingerprint = file.Length.ToString(CultureInfo.InvariantCulture) + "|" +
                file.LastWriteTimeUtc.Ticks.ToString(CultureInfo.InvariantCulture);
            string key = "artifact|" + path;
            string previous;
            if (_fingerprints.TryGetValue(key, out previous) &&
                String.Equals(previous, fingerprint, StringComparison.Ordinal))
            {
                return;
            }

            bool sensitiveName = SensitiveArtifactName.IsMatch(file.Name);
            string safePath = sensitiveName
                ? "[REDACTED SENSITIVE ARTIFACT PATH]"
                : SanitizeText(file.FullName);
            UiItem item = new UiItem();
            item.TimestampUtc = file.LastWriteTimeUtc.ToString("o");
            item.Lane = "artifact";
            item.Source = "artifact-adapter";
            item.RunId = file.Directory == null ? "unscoped" : SanitizeText(file.Directory.Name);
            item.Kind = "artifact.updated";
            item.Status = sensitiveName ? "redacted" : "observed";
            item.Stage = "artifact";
            item.Summary = file.Name + " | " + file.Length.ToString(CultureInfo.InvariantCulture) + " bytes";
            item.Detail = "Artifact contents are not opened by the generic adapter.";
            item.Artifact = safePath;
            item.Origin = safePath;
            _fingerprints[key] = fingerprint;
            Enqueue(item);
        }

        private UiItem BuildEventItem(Dictionary<string, object> root, string path)
        {
            object metadataObject = GetObject(root, "metadata");
            Dictionary<string, object> metadata = metadataObject as Dictionary<string, object>;
            UiItem item = new UiItem();
            item.TimestampUtc = GetString(root, "timestamp_utc", "timestamp", "time");
            if (String.IsNullOrWhiteSpace(item.TimestampUtc))
            {
                item.TimestampUtc = File.GetLastWriteTimeUtc(path).ToString("o");
            }
            item.Lane = Default(GetString(root, "lane", "client", "provider"), "unassigned");
            item.Source = Default(GetString(root, "source", "process", "producer"), "unknown");
            item.RunId = Default(GetString(root, "run_id", "run", "job_id"), "unscoped");
            item.Kind = Default(GetString(root, "kind", "event", "type"), "event");
            item.Status = Default(GetString(root, "status", "state"), "observed");
            item.Stage = GetString(root, "stage", "phase");
            item.Summary = GetDisplay(root, "summary", "message", "title");
            item.Detail = GetDisplay(root, "detail", "description");
            item.Command = FirstNonEmpty(
                GetDisplay(root, "command", "cmd", "command_line"),
                GetDisplay(metadata, "command", "cmd", "command_line"));
            item.Input = FirstNonEmpty(
                GetDisplay(root, "input", "inputs", "request", "query"),
                GetDisplay(metadata, "input", "inputs", "request", "query"));
            item.Output = FirstNonEmpty(
                GetDisplay(root, "output", "outputs", "response", "result"),
                GetDisplay(metadata, "output", "outputs", "response", "result"));
            item.Artifact = FirstNonEmpty(
                GetDisplay(root, "artifact", "artifacts", "path", "file"),
                GetDisplay(metadata, "artifact", "artifacts", "path", "file"));
            item.Failure = FirstNonEmpty(
                GetDisplay(root, "failure", "error", "exception"),
                GetDisplay(metadata, "failure", "error", "exception"));
            item.Metadata = SerializeSanitized(metadataObject);
            item.Origin = SanitizeText(path);
            ExtractFraction(item, item.Summary + " " + item.Detail);
            if (item.Current < 0 || item.Total <= 0)
            {
                ExtractProgressFields(item, root);
            }
            return item;
        }

        private UiItem BuildProgressItem(Dictionary<string, object> root, string path)
        {
            UiItem item = new UiItem();
            item.TimestampUtc = GetString(root, "updated_at", "timestamp_utc", "timestamp", "time");
            if (String.IsNullOrWhiteSpace(item.TimestampUtc))
            {
                item.TimestampUtc = File.GetLastWriteTimeUtc(path).ToString("o");
            }
            item.Lane = Default(GetString(root, "lane", "client", "provider"), "adapter");
            item.Source = Default(
                GetString(root, "source", "process", "producer"),
                Path.GetFileNameWithoutExtension(path));
            item.RunId = Default(
                GetString(root, "run_id", "run", "job_id"),
                new FileInfo(path).Directory == null
                    ? "unscoped"
                    : new FileInfo(path).Directory.Name);
            item.Kind = Default(GetString(root, "kind", "event", "type"), "progress.adapter");
            item.Status = Default(GetString(root, "status", "state"), "running");
            item.Stage = Default(GetString(root, "stage", "phase"), "progress");
            ExtractProgressFields(item, root);
            string suppliedSummary = GetDisplay(root, "summary", "message", "title");
            if (!String.IsNullOrWhiteSpace(suppliedSummary))
            {
                item.Summary = suppliedSummary;
            }
            else if (item.Current >= 0 && item.Total > 0)
            {
                int percent = (int)Math.Floor((item.Current / (double)item.Total) * 100.0);
                item.Summary = item.Current.ToString(CultureInfo.InvariantCulture) + "/" +
                    item.Total.ToString(CultureInfo.InvariantCulture) + " (" +
                    percent.ToString(CultureInfo.InvariantCulture) + "%)";
            }
            else
            {
                item.Summary = "Progress artifact updated";
            }
            item.Detail = BuildProgressDetail(root);
            item.Metadata = SerializeSanitized(root);
            item.Artifact = SanitizeText(path);
            item.Origin = SanitizeText(path);
            return item;
        }

        private string BuildProgressDetail(Dictionary<string, object> root)
        {
            string[] keys = new string[] {
                "active_workers", "workers", "accepted_pages", "pages",
                "failure_count", "failures", "eta_seconds", "eta",
                "queries_per_minute", "rate"
            };
            List<string> parts = new List<string>();
            foreach (string key in keys)
            {
                object value;
                if (root != null && root.TryGetValue(key, out value) && value != null)
                {
                    parts.Add(key + "=" + SerializeSanitized(value));
                }
            }
            return String.Join(" | ", parts.ToArray());
        }

        private void ExtractProgressFields(UiItem item, Dictionary<string, object> root)
        {
            item.Current = GetInt(
                root,
                "completed_queries", "completed", "current", "done",
                "processed", "accepted", "finished");
            item.Total = GetInt(
                root,
                "total_queries", "total", "required", "target", "expected");
            if (item.Current < 0 || item.Total <= 0)
            {
                ExtractFraction(item, GetDisplay(root, "summary", "message", "title"));
            }
        }

        private void ExtractFraction(UiItem item, string text)
        {
            if (String.IsNullOrWhiteSpace(text)) return;
            Match match = Fraction.Match(text);
            if (!match.Success) return;
            int current;
            int total;
            if (Int32.TryParse(match.Groups["current"].Value, out current) &&
                Int32.TryParse(match.Groups["total"].Value, out total) &&
                total > 0)
            {
                item.Current = current;
                item.Total = total;
            }
        }

        private int GetInt(Dictionary<string, object> root, params string[] names)
        {
            if (root == null) return -1;
            foreach (string name in names)
            {
                object value;
                if (!root.TryGetValue(name, out value) || value == null) continue;
                int parsed;
                if (Int32.TryParse(
                    Convert.ToString(value, CultureInfo.InvariantCulture),
                    NumberStyles.Integer,
                    CultureInfo.InvariantCulture,
                    out parsed))
                {
                    return parsed;
                }
            }
            return -1;
        }

        private object GetObject(Dictionary<string, object> root, string name)
        {
            if (root == null) return null;
            object value;
            return root.TryGetValue(name, out value) ? value : null;
        }

        private string GetString(Dictionary<string, object> root, params string[] names)
        {
            if (root == null) return "";
            foreach (string name in names)
            {
                object value;
                if (root.TryGetValue(name, out value) && value != null)
                {
                    string text = Convert.ToString(value, CultureInfo.InvariantCulture);
                    if (!String.IsNullOrWhiteSpace(text)) return SanitizeText(text);
                }
            }
            return "";
        }

        private string GetDisplay(Dictionary<string, object> root, params string[] names)
        {
            if (root == null) return "";
            foreach (string name in names)
            {
                object value;
                if (!root.TryGetValue(name, out value) || value == null) continue;
                string display = value is string
                    ? SanitizeText((string)value)
                    : SerializeSanitized(value);
                if (!String.IsNullOrWhiteSpace(display)) return display;
            }
            return "";
        }

        private string SerializeSanitized(object value)
        {
            if (value == null) return "";
            try
            {
                object clean = SanitizeObject(value, null, 0);
                string serialized = clean is string
                    ? (string)clean
                    : _json.Serialize(clean);
                return Limit(SanitizeText(serialized), 12000);
            }
            catch
            {
                return "[UNAVAILABLE]";
            }
        }

        private object SanitizeObject(object value, string key, int depth)
        {
            if (depth > 20) return "[DEPTH LIMITED]";
            if (!String.IsNullOrWhiteSpace(key) && SensitiveKey.IsMatch(key))
            {
                return "[REDACTED]";
            }
            if (value == null) return null;
            string stringValue = value as string;
            if (stringValue != null) return SanitizeText(stringValue);

            Dictionary<string, object> dictionary = value as Dictionary<string, object>;
            if (dictionary != null)
            {
                Dictionary<string, object> clean = new Dictionary<string, object>();
                foreach (KeyValuePair<string, object> pair in dictionary)
                {
                    clean[pair.Key] = SanitizeObject(pair.Value, pair.Key, depth + 1);
                }
                return clean;
            }

            object[] array = value as object[];
            if (array != null)
            {
                return array.Select(v => SanitizeObject(v, null, depth + 1)).ToArray();
            }

            ArrayList list = value as ArrayList;
            if (list != null)
            {
                ArrayList clean = new ArrayList();
                foreach (object element in list)
                {
                    clean.Add(SanitizeObject(element, null, depth + 1));
                }
                return clean;
            }
            return value;
        }

        private static string SanitizeText(string text)
        {
            if (String.IsNullOrEmpty(text)) return "";
            string clean = SensitiveFlag.Replace(text, "$1[REDACTED]");
            clean = SensitiveAssignment.Replace(clean, "$1[REDACTED]");
            clean = BearerValue.Replace(clean, "Bearer [REDACTED]");
            clean = SensitiveQuery.Replace(clean, "$1[REDACTED]");
            return Limit(clean, 12000);
        }

        private bool TryReadStableText(string path, out string text)
        {
            text = null;
            for (int attempt = 0; attempt < 3; attempt += 1)
            {
                try
                {
                    using (FileStream stream = new FileStream(
                        path,
                        FileMode.Open,
                        FileAccess.Read,
                        FileShare.ReadWrite | FileShare.Delete))
                    using (StreamReader reader = new StreamReader(
                        stream,
                        Encoding.UTF8,
                        true,
                        4096,
                        false))
                    {
                        text = reader.ReadToEnd();
                    }
                    if (!String.IsNullOrWhiteSpace(text)) return true;
                }
                catch (IOException)
                {
                    Thread.Sleep(35 * (attempt + 1));
                }
                catch (UnauthorizedAccessException)
                {
                    return false;
                }
            }
            return false;
        }

        private void Enqueue(UiItem item)
        {
            if (item == null) return;
            _output.Enqueue(item);
            int count = Interlocked.Increment(ref _queuedItems);
            while (count > _maxQueueItems)
            {
                UiItem discarded;
                if (!_output.TryDequeue(out discarded)) break;
                Interlocked.Decrement(ref _queuedItems);
                Interlocked.Increment(ref _droppedItems);
                count = Volatile.Read(ref _queuedItems);
            }
        }

        private UiItem SystemItem(string kind, string status, string detail)
        {
            UiItem item = new UiItem();
            item.TimestampUtc = DateTime.UtcNow.ToString("o");
            item.Lane = "viewer";
            item.Source = "process-matrix";
            item.RunId = "viewer";
            item.Kind = kind;
            item.Status = status;
            item.Stage = "viewer";
            item.Summary = kind;
            item.Detail = SanitizeText(detail);
            return item;
        }

        private static string Sha256(string text)
        {
            using (SHA256 sha = SHA256.Create())
            {
                byte[] bytes = Encoding.UTF8.GetBytes(text ?? "");
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            }
        }

        private static string FirstNonEmpty(params string[] values)
        {
            foreach (string value in values)
            {
                if (!String.IsNullOrWhiteSpace(value)) return value;
            }
            return "";
        }

        private static string Default(string value, string fallback)
        {
            return String.IsNullOrWhiteSpace(value) ? fallback : value;
        }

        private static string Limit(string value, int maximum)
        {
            if (String.IsNullOrEmpty(value) || value.Length <= maximum) return value ?? "";
            return value.Substring(0, maximum) + " ... [TRUNCATED]";
        }
    }
}
'@

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Web.Extensions
Add-Type -TypeDefinition $EngineSource -ReferencedAssemblies @(
    "System.dll",
    "System.Core.dll",
    "System.Web.Extensions.dll"
)

if ($ValidateOnly) {
    $ValidationEngine = [GlassboxProcessMatrix.Engine]::new(
        $ResolvedEventsDirectory,
        [string[]]$ResolvedProgressJson,
        [string[]]$ResolvedArtifactPath,
        $PollMilliseconds,
        $ReplayEvents,
        $ReplayArtifacts,
        $MaxQueueItems
    )
    $ValidationEngine.Dispose()
    Write-Output "PASS: open_glassbox_process_matrix.ps1 syntax and background engine compiled."
    Write-Output "Events: $ResolvedEventsDirectory"
    exit 0
}

[System.Windows.Forms.Application]::EnableVisualStyles()

$Engine = [GlassboxProcessMatrix.Engine]::new(
    $ResolvedEventsDirectory,
    [string[]]$ResolvedProgressJson,
    [string[]]$ResolvedArtifactPath,
    $PollMilliseconds,
    $ReplayEvents,
    $ReplayArtifacts,
    $MaxQueueItems
)

$Form = New-Object System.Windows.Forms.Form
$Form.Text = $WindowTitle
$Form.StartPosition = "CenterScreen"
$Form.FormBorderStyle = "Sizable"
$Form.MaximizeBox = $true
$Form.MinimizeBox = $true
$Form.ControlBox = $true
$Form.ShowInTaskbar = $true
$Form.AutoScaleMode = "Dpi"
$Form.Size = New-Object System.Drawing.Size(1540, 940)
$Form.MinimumSize = New-Object System.Drawing.Size(1000, 650)
$Form.BackColor = [System.Drawing.Color]::FromArgb(15, 18, 24)
$Form.ForeColor = [System.Drawing.Color]::Gainsboro
$Form.Font = New-Object System.Drawing.Font("Consolas", 9)

$Split = New-Object System.Windows.Forms.SplitContainer
$Split.Dock = "Fill"
$Split.Orientation = "Horizontal"
$Form.Controls.Add($Split)
$Split.SplitterDistance = 330
$Split.Panel1MinSize = 230
$Split.Panel2MinSize = 260
$Split.IsSplitterFixed = $false
$Split.BackColor = [System.Drawing.Color]::FromArgb(42, 48, 58)

$Overhead = $Split.Panel1
$Overhead.BackColor = [System.Drawing.Color]::FromArgb(15, 18, 24)
$FeedPanel = $Split.Panel2
$FeedPanel.BackColor = [System.Drawing.Color]::FromArgb(10, 12, 17)

$Title = New-Object System.Windows.Forms.Label
$Title.Text = "OVERHEAD / ALL OBSERVED PROCESSES"
$Title.ForeColor = [System.Drawing.Color]::White
$Title.Font = New-Object System.Drawing.Font("Consolas", 14, [System.Drawing.FontStyle]::Bold)
$Title.Location = New-Object System.Drawing.Point(15, 10)
$Title.AutoSize = $true
$Overhead.Controls.Add($Title)

$Overall = New-Object System.Windows.Forms.Label
$Overall.Text = "Waiting for Glassbox events or an attached progress artifact..."
$Overall.ForeColor = [System.Drawing.Color]::Cyan
$Overall.Font = New-Object System.Drawing.Font("Consolas", 11, [System.Drawing.FontStyle]::Bold)
$Overall.Location = New-Object System.Drawing.Point(16, 43)
$Overall.Size = New-Object System.Drawing.Size(1490, 24)
$Overall.Anchor = "Top,Left,Right"
$Overhead.Controls.Add($Overall)

$ProgressBar = New-Object System.Windows.Forms.ProgressBar
$ProgressBar.Minimum = 0
$ProgressBar.Maximum = 100
$ProgressBar.Value = 0
$ProgressBar.Style = "Continuous"
$ProgressBar.Location = New-Object System.Drawing.Point(18, 70)
$ProgressBar.Size = New-Object System.Drawing.Size(1480, 22)
$ProgressBar.Anchor = "Top,Left,Right"
$Overhead.Controls.Add($ProgressBar)

$SourceStatus = New-Object System.Windows.Forms.Label
$SourceStatus.Text = "EVENTS  $ResolvedEventsDirectory"
$SourceStatus.ForeColor = [System.Drawing.Color]::DarkGray
$SourceStatus.Location = New-Object System.Drawing.Point(18, 98)
$SourceStatus.Size = New-Object System.Drawing.Size(1480, 19)
$SourceStatus.Anchor = "Top,Left,Right"
$Overhead.Controls.Add($SourceStatus)

$Matrix = New-Object System.Windows.Forms.DataGridView
$Matrix.Location = New-Object System.Drawing.Point(18, 122)
$Matrix.Size = New-Object System.Drawing.Size(1480, 194)
$Matrix.Anchor = "Top,Bottom,Left,Right"
$Matrix.BackgroundColor = [System.Drawing.Color]::FromArgb(20, 24, 31)
$Matrix.BorderStyle = "FixedSingle"
$Matrix.EnableHeadersVisualStyles = $false
$Matrix.ColumnHeadersDefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(38, 45, 56)
$Matrix.ColumnHeadersDefaultCellStyle.ForeColor = [System.Drawing.Color]::White
$Matrix.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 31)
$Matrix.DefaultCellStyle.ForeColor = [System.Drawing.Color]::Gainsboro
$Matrix.DefaultCellStyle.SelectionBackColor = [System.Drawing.Color]::FromArgb(50, 70, 90)
$Matrix.DefaultCellStyle.SelectionForeColor = [System.Drawing.Color]::White
$Matrix.RowHeadersVisible = $false
$Matrix.AllowUserToAddRows = $false
$Matrix.AllowUserToDeleteRows = $false
$Matrix.AllowUserToOrderColumns = $true
$Matrix.AllowUserToResizeColumns = $true
$Matrix.AllowUserToResizeRows = $false
$Matrix.ReadOnly = $true
$Matrix.SelectionMode = "FullRowSelect"
$Matrix.AutoSizeColumnsMode = "Fill"
[void]$Matrix.Columns.Add("lane", "LANE")
[void]$Matrix.Columns.Add("source", "SOURCE / PROCESS")
[void]$Matrix.Columns.Add("run", "RUN")
[void]$Matrix.Columns.Add("status", "STATUS")
[void]$Matrix.Columns.Add("kind", "LATEST EVENT")
[void]$Matrix.Columns.Add("progress", "PROGRESS")
[void]$Matrix.Columns.Add("updated", "UPDATED")
$Matrix.Columns["lane"].FillWeight = 10
$Matrix.Columns["source"].FillWeight = 22
$Matrix.Columns["run"].FillWeight = 20
$Matrix.Columns["status"].FillWeight = 10
$Matrix.Columns["kind"].FillWeight = 18
$Matrix.Columns["progress"].FillWeight = 10
$Matrix.Columns["updated"].FillWeight = 10
$Overhead.Controls.Add($Matrix)

$FeedTitle = New-Object System.Windows.Forms.Label
$FeedTitle.Text = "LIVE FEED / SANITIZED COMMANDS + INPUTS + OUTPUTS + ARTIFACTS + FAILURES + METADATA"
$FeedTitle.ForeColor = [System.Drawing.Color]::White
$FeedTitle.Font = New-Object System.Drawing.Font("Consolas", 10, [System.Drawing.FontStyle]::Bold)
$FeedTitle.Location = New-Object System.Drawing.Point(10, 9)
$FeedTitle.AutoSize = $true
$FeedPanel.Controls.Add($FeedTitle)

$PauseButton = New-Object System.Windows.Forms.Button
$PauseButton.Text = "Pause feed"
$PauseButton.Size = New-Object System.Drawing.Size(92, 25)
$PauseButton.Anchor = "Top,Right"
$PauseButton.Location = New-Object System.Drawing.Point(1190, 5)
$FeedPanel.Controls.Add($PauseButton)

$ClearButton = New-Object System.Windows.Forms.Button
$ClearButton.Text = "Clear"
$ClearButton.Size = New-Object System.Drawing.Size(66, 25)
$ClearButton.Anchor = "Top,Right"
$ClearButton.Location = New-Object System.Drawing.Point(1288, 5)
$FeedPanel.Controls.Add($ClearButton)

$Filter = New-Object System.Windows.Forms.ComboBox
$Filter.DropDownStyle = "DropDownList"
$Filter.Size = New-Object System.Drawing.Size(155, 25)
$Filter.Anchor = "Top,Right"
$Filter.Location = New-Object System.Drawing.Point(1360, 5)
[void]$Filter.Items.Add("All sources")
$Filter.SelectedIndex = 0
$FeedPanel.Controls.Add($Filter)

$Feed = New-Object System.Windows.Forms.RichTextBox
$Feed.Location = New-Object System.Drawing.Point(10, 35)
$Feed.Size = New-Object System.Drawing.Size(1505, 520)
$Feed.Anchor = "Top,Bottom,Left,Right"
$Feed.BackColor = [System.Drawing.Color]::FromArgb(8, 10, 14)
$Feed.ForeColor = [System.Drawing.Color]::Gainsboro
$Feed.BorderStyle = "FixedSingle"
$Feed.ReadOnly = $true
$Feed.WordWrap = $false
$Feed.DetectUrls = $false
$Feed.HideSelection = $false
$Feed.Font = New-Object System.Drawing.Font("Consolas", 9)
$FeedPanel.Controls.Add($Feed)

$RowsByKey = @{}
$Sources = @{}
$LatestProgressItem = $null
$Paused = $false
$ReceivedItems = 0

function Get-EventTimeText {
    param([string]$Timestamp)
    try {
        return ([datetimeoffset]::Parse($Timestamp)).ToLocalTime().ToString("HH:mm:ss")
    }
    catch {
        return (Get-Date -Format "HH:mm:ss")
    }
}

function Add-FeedText {
    param(
        [string]$Text,
        [System.Drawing.Color]$Color = [System.Drawing.Color]::Gainsboro
    )
    if ([string]::IsNullOrWhiteSpace($Text)) { return }
    $Feed.SelectionStart = $Feed.TextLength
    $Feed.SelectionLength = 0
    $Feed.SelectionColor = $Color
    $Feed.AppendText($Text)
    if (-not $Text.EndsWith("`r`n")) {
        $Feed.AppendText("`r`n")
    }
    $Feed.SelectionColor = $Feed.ForeColor
}

function Trim-Feed {
    if ($Feed.TextLength -le $MaxFeedCharacters) { return }
    $TargetRemove = [Math]::Max(1, [int]($Feed.TextLength * 0.25))
    $Break = $Feed.Text.IndexOf("`n", $TargetRemove)
    if ($Break -lt 0) { $Break = $TargetRemove }
    $Feed.Select(0, [Math]::Min($Feed.TextLength, $Break + 1))
    $Feed.SelectedText = ""
}

function Get-ItemProgressText {
    param($Item)
    if ([int]$Item.Current -ge 0 -and [int]$Item.Total -gt 0) {
        $Percent = [Math]::Floor(([double]$Item.Current / [double]$Item.Total) * 100)
        return "$($Item.Current)/$($Item.Total) ($Percent%)"
    }
    return "--"
}

function Update-MatrixRow {
    param($Item)
    $Key = [string]$Item.MatrixKey
    $Progress = Get-ItemProgressText $Item
    $Updated = Get-EventTimeText $Item.TimestampUtc
    if ($RowsByKey.ContainsKey($Key)) {
        $Row = $RowsByKey[$Key]
        $Row.SetValues(
            $Item.Lane,
            $Item.Source,
            $Item.RunId,
            $Item.Status,
            $Item.Kind,
            $Progress,
            $Updated
        )
    }
    else {
        $Index = $Matrix.Rows.Add(
            $Item.Lane,
            $Item.Source,
            $Item.RunId,
            $Item.Status,
            $Item.Kind,
            $Progress,
            $Updated
        )
        $RowsByKey[$Key] = $Matrix.Rows[$Index]
    }

    if ([string]$Item.Status -match "fail|error|blocked") {
        $RowsByKey[$Key].DefaultCellStyle.ForeColor = [System.Drawing.Color]::OrangeRed
    }
    elseif ([string]$Item.Status -match "complete|pass|success") {
        $RowsByKey[$Key].DefaultCellStyle.ForeColor = [System.Drawing.Color]::LimeGreen
    }
    else {
        $RowsByKey[$Key].DefaultCellStyle.ForeColor = [System.Drawing.Color]::Gainsboro
    }
}

function Update-OverallProgress {
    param($Item)
    if ([int]$Item.Current -lt 0 -or [int]$Item.Total -le 0) { return }
    $script:LatestProgressItem = $Item
    $Percent = [Math]::Max(
        0,
        [Math]::Min(
            100,
            [int][Math]::Floor(([double]$Item.Current / [double]$Item.Total) * 100)
        )
    )
    $ProgressBar.Value = $Percent
    $Overall.Text = (
        "$($Item.Source) | $($Item.RunId) | $($Item.Current)/$($Item.Total) " +
        "($Percent%) | $([string]$Item.Status).ToUpperInvariant() | $($Item.Summary)"
    )
    if ([string]$Item.Status -match "fail|error|blocked") {
        $Overall.ForeColor = [System.Drawing.Color]::OrangeRed
    }
    elseif ($Percent -ge 100 -or [string]$Item.Status -match "complete|pass|success") {
        $Overall.ForeColor = [System.Drawing.Color]::LimeGreen
    }
    else {
        $Overall.ForeColor = [System.Drawing.Color]::Cyan
    }
}

function Test-FeedFilter {
    param($Item)
    if ($Filter.SelectedIndex -le 0) { return $true }
    return [string]$Item.Source -eq [string]$Filter.SelectedItem
}

function Add-ItemToFeed {
    param($Item)
    if ($Paused -or -not (Test-FeedFilter $Item)) { return }
    $Time = Get-EventTimeText $Item.TimestampUtc
    $HeaderColor = [System.Drawing.Color]::MediumTurquoise
    if ([string]$Item.Status -match "fail|error|blocked") {
        $HeaderColor = [System.Drawing.Color]::OrangeRed
    }
    elseif ([string]$Item.Status -match "complete|pass|success") {
        $HeaderColor = [System.Drawing.Color]::LimeGreen
    }
    Add-FeedText (
        "`r`n[$Time] $([string]$Item.Lane).ToUpperInvariant() / $($Item.Source) " +
        "| run=$($Item.RunId) | $($Item.Kind) | $($Item.Status)"
    ) $HeaderColor
    Add-FeedText ("  SUMMARY   " + [string]$Item.Summary) ([System.Drawing.Color]::White)
    if ($Item.Detail) {
        Add-FeedText ("  DETAIL    " + [string]$Item.Detail) ([System.Drawing.Color]::Silver)
    }
    if ($Item.Command) {
        Add-FeedText ("  COMMAND   " + [string]$Item.Command) ([System.Drawing.Color]::DeepSkyBlue)
    }
    if ($Item.Input) {
        Add-FeedText ("  INPUT     " + [string]$Item.Input) ([System.Drawing.Color]::Khaki)
    }
    if ($Item.Output) {
        Add-FeedText ("  OUTPUT    " + [string]$Item.Output) ([System.Drawing.Color]::LightGreen)
    }
    if ($Item.Artifact) {
        Add-FeedText ("  ARTIFACT  " + [string]$Item.Artifact) ([System.Drawing.Color]::DarkGray)
    }
    if ($Item.Failure) {
        Add-FeedText ("  FAILURE   " + [string]$Item.Failure) ([System.Drawing.Color]::OrangeRed)
    }
    if ($Item.Metadata) {
        Add-FeedText ("  METADATA  " + [string]$Item.Metadata) ([System.Drawing.Color]::Gray)
    }
    if ($Item.Origin) {
        Add-FeedText ("  ORIGIN    " + [string]$Item.Origin) ([System.Drawing.Color]::DimGray)
    }
}

$PauseButton.Add_Click({
    $script:Paused = -not $Paused
    if ($Paused) {
        $PauseButton.Text = "Resume feed"
        $PauseButton.BackColor = [System.Drawing.Color]::FromArgb(120, 75, 25)
    }
    else {
        $PauseButton.Text = "Pause feed"
        $PauseButton.BackColor = [System.Drawing.SystemColors]::Control
    }
})

$ClearButton.Add_Click({
    $Feed.Clear()
    Add-FeedText (
        "[$(Get-Date -Format 'HH:mm:ss')] Feed display cleared; observation continues."
    ) ([System.Drawing.Color]::DarkGray)
})

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 100
$Timer.Add_Tick({
    try {
        $Consumed = 0
        while ($Consumed -lt [Math]::Max(1, [Math]::Min(500, $UiBatchSize))) {
            $Item = $null
            if (-not $Engine.TryTake([ref]$Item)) { break }
            $Consumed += 1
            $script:ReceivedItems += 1
            Update-MatrixRow $Item
            Update-OverallProgress $Item
            if (-not $Sources.ContainsKey([string]$Item.Source)) {
                $Sources[[string]$Item.Source] = $true
                [void]$Filter.Items.Add([string]$Item.Source)
            }
            Add-ItemToFeed $Item
        }
        if ($Consumed -gt 0 -and -not $Paused) {
            Trim-Feed
            $Feed.SelectionStart = $Feed.TextLength
            $Feed.ScrollToCaret()
        }
        $SourceStatus.Text = (
            "EVENTS  $ResolvedEventsDirectory | rows=$($RowsByKey.Count) | " +
            "received=$ReceivedItems | queued=$($Engine.QueuedItems) | " +
            "dropped=$($Engine.DroppedItems) | parsing=background"
        )
    }
    catch {
        Add-FeedText (
            "[$(Get-Date -Format 'HH:mm:ss')] VIEWER UI ERROR $($_.Exception.Message)"
        ) ([System.Drawing.Color]::OrangeRed)
    }
})

$Form.Add_Shown({
    Add-FeedText (
        "GLASS BOX PROCESS MATRIX ATTACHED  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    ) ([System.Drawing.Color]::White)
    Add-FeedText (
        "Events and adapters are parsed on a background thread; the UI drains a bounded queue."
    ) ([System.Drawing.Color]::DarkGray)
    Add-FeedText (
        "Sensitive keys, command flags, authorization values, cookies, and token-like URL parameters are redacted."
    ) ([System.Drawing.Color]::DarkGray)
    $Engine.Start()
    $Timer.Start()
})

$Form.Add_FormClosed({
    $Timer.Stop()
    $Timer.Dispose()
    $Engine.Dispose()
})

[void]$Form.ShowDialog()
