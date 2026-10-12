[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
# Render the same simple geometric mark as mentat-icon.svg at every Windows size.
$iconPath = Join-Path $PSScriptRoot 'MENTAT.ico'
$images = @()
foreach ($size in @(16, 24, 32, 48, 64, 128, 256)) {
  $bitmap = New-Object Drawing.Bitmap($size, $size)
  $graphics = [Drawing.Graphics]::FromImage($bitmap)
  $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.ScaleTransform($size / 256.0, $size / 256.0)
  $plum = New-Object Drawing.SolidBrush([Drawing.ColorTranslator]::FromHtml('#342649'))
  $ivory = New-Object Drawing.SolidBrush([Drawing.ColorTranslator]::FromHtml('#F7EDE4'))
  $gold = New-Object Drawing.SolidBrush([Drawing.ColorTranslator]::FromHtml('#ECA91C'))
  $rounded = New-Object Drawing.Drawing2D.GraphicsPath
  $rounded.AddArc(8,8,104,104,180,90)
  $rounded.AddArc(144,8,104,104,270,90)
  $rounded.AddArc(144,144,104,104,0,90)
  $rounded.AddArc(8,144,104,104,90,90)
  $rounded.CloseFigure()
  $graphics.FillPath($plum, $rounded)
  $points = [Drawing.PointF[]]@(
    (New-Object Drawing.PointF(55,184)), (New-Object Drawing.PointF(55,72)),
    (New-Object Drawing.PointF(84,72)), (New-Object Drawing.PointF(128,133)),
    (New-Object Drawing.PointF(172,72)), (New-Object Drawing.PointF(201,72)),
    (New-Object Drawing.PointF(201,184)), (New-Object Drawing.PointF(172,184)),
    (New-Object Drawing.PointF(172,119)), (New-Object Drawing.PointF(128,177)),
    (New-Object Drawing.PointF(84,119)), (New-Object Drawing.PointF(84,184)))
  $graphics.FillPolygon($ivory, $points)
  $graphics.FillEllipse($gold, 186,40,28,28)
  $stream = New-Object IO.MemoryStream
  $bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
  if ($size -eq 256) { $bitmap.Save((Join-Path $PSScriptRoot 'MENTAT.png'), [Drawing.Imaging.ImageFormat]::Png) }
  $images += ,($stream.ToArray())
  $stream.Dispose(); $graphics.Dispose(); $bitmap.Dispose()
  $rounded.Dispose(); $plum.Dispose(); $ivory.Dispose(); $gold.Dispose()
}
$file = [IO.File]::Create($iconPath)
$writer = New-Object IO.BinaryWriter($file)
try {
  $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$images.Count)
  $offset = 6 + 16 * $images.Count
  $sizes = @(16,24,32,48,64,128,256)
  for ($i=0; $i -lt $images.Count; $i++) {
    $dimension = $sizes[$i] % 256
    $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
    $writer.Write([byte]0); $writer.Write([byte]0)
    $writer.Write([uint16]1); $writer.Write([uint16]32)
    $writer.Write([uint32]$images[$i].Length); $writer.Write([uint32]$offset)
    $offset += $images[$i].Length
  }
  foreach ($bytes in $images) { $writer.Write([byte[]]$bytes) }
} finally { $writer.Dispose(); $file.Dispose() }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
& $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll "/win32icon:$iconPath" "/out:$PSScriptRoot\MENTAT.exe" "$PSScriptRoot\Launcher.cs"
if ($LASTEXITCODE -ne 0) { throw 'The MENTAT desktop launcher could not be built.' }
Write-Host 'MENTAT desktop executable and icon built.'
