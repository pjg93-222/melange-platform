using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Launcher
{
    [STAThread]
    private static int Main()
    {
        string errorLog = Path.Combine(Path.GetTempPath(), "MENTAT-launch-" + Guid.NewGuid().ToString("N") + ".log");
        try
        {
            // A window title does not prove its server supports the current
            // frontend. Always let the launcher validate the loaded version
            // and open that origin, including when an older window remains.
            string script = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "Start-MentatApp.ps1"));
            if (!File.Exists(script)) throw new FileNotFoundException("The MENTAT application folder is missing. Run Install MENTAT again.");
            var start = new ProcessStartInfo("powershell.exe", "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\" -AppWindow -ErrorLog \"" + errorLog + "\"") {
                UseShellExecute = false, CreateNoWindow = true
            };
            using (var process = Process.Start(start))
            {
                process.WaitForExit();
                if (process.ExitCode != 0) throw new Exception(File.Exists(errorLog) ? File.ReadAllText(errorLog).Trim() : "Check the local startup logs for details.");
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show("MENTAT could not open.\n\n" + error.Message +
                "\n\nStartup logs: " + Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Mentat", "console"),
                "MENTAT", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally { if (File.Exists(errorLog)) File.Delete(errorLog); }
    }
}
