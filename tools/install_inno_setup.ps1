param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$compilerDirectory = Join-Path ([IO.Path]::GetFullPath($Destination)) 'compiler'
$compiler = Join-Path $compilerDirectory 'ISCC.exe'
if (Test-Path -LiteralPath $compiler) {
    if ((Get-Item -LiteralPath $compiler).VersionInfo.FileVersion -notlike '6.7.3*') {
        throw "Expected Inno Setup 6.7.3: $compiler"
    }
    Write-Output $compiler
    return
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$download = Join-Path ([IO.Path]::GetFullPath($Destination)) 'innosetup-6.7.3.exe'
if (-not (Test-Path -LiteralPath $download)) {
    Invoke-WebRequest 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $download
}
$signature = Get-AuthenticodeSignature -LiteralPath $download
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Pyrsys B\.V\.') {
    throw 'The Inno Setup download has no valid signature from its official publisher.'
}
# The upstream installer's portable mode creates no shortcuts, file associations
# or uninstall entry. Keep the build tool inside the requested build directory.
$installation = Start-Process -FilePath $download -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CURRENTUSER',
    '/PORTABLE=1', '/NOICONS', ('/DIR="{0}"' -f $compilerDirectory)
) -Wait -PassThru -WindowStyle Hidden
if ($installation.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $compiler)) {
    throw "Inno Setup tool installation failed: $($installation.ExitCode)"
}
Write-Output $compiler
