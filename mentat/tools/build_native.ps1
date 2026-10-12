param([switch]$Test)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$cached = Join-Path $project '.cache\rust-tools'
$cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
if (Test-Path (Join-Path $cached 'cargo\bin\cargo.exe')) {
  $env:CARGO_HOME = Join-Path $cached 'cargo'
  $env:RUSTUP_HOME = Join-Path $cached 'rustup'
  $cargo = Join-Path $env:CARGO_HOME 'bin\cargo.exe'
  $env:PATH = (Join-Path $env:CARGO_HOME 'bin') + ';' + $env:PATH
  $toolchain = Join-Path $env:RUSTUP_HOME 'toolchains\stable-x86_64-pc-windows-gnu'
  $linker = Join-Path $toolchain 'lib\rustlib\x86_64-pc-windows-gnu\bin\rust-lld.exe'
  if (Test-Path $linker) {
    $env:CARGO_ENCODED_RUSTFLAGS = @('-C',('linker=' + $linker),'-C','linker-flavor=ld.lld','-C','link-self-contained=yes') -join [char]31
  }
} elseif ($cargoCommand) { $cargo = $cargoCommand.Source }
else { throw 'Rust is unavailable. Install Rust or place a minimal toolchain in .cache/rust-tools; the Python fallback remains usable.' }
$env:CARGO_TARGET_DIR = Join-Path $project '.cache\native-target'
$manifest = Join-Path $project 'native\mentat-scan\Cargo.toml'
if ($Test) { & $cargo test --manifest-path $manifest --locked; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
& $cargo build --manifest-path $manifest --release --locked
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Write-Output ('Native scanner: ' + (Join-Path $env:CARGO_TARGET_DIR 'release\mentat-scan.exe'))
