param(
    [Parameter(Mandatory=$true)][string]$InputPdf,
    [Parameter(Mandatory=$true)][string]$OutputPng
)
# Read-only QA renderer using the Windows PDF API, not UI automation.
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader,Windows.Storage.Streams,ContentType=WindowsRuntime]
$operationMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
$actionMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
} | Select-Object -First 1
function Wait-Operation($Operation, [Type]$ResultType) {
    $task = $operationMethod.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    $task.GetAwaiter().GetResult()
}
$inputPath = (Resolve-Path -LiteralPath $InputPdf).Path
$file = Wait-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($inputPath)) ([Windows.Storage.StorageFile])
$document = Wait-Operation ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
if ($document.PageCount -lt 1) { throw 'PDF has no pages.' }
$page = $document.GetPage(0)
$stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
try {
    $task = $actionMethod.Invoke($null,@($page.RenderToStreamAsync($stream)))
    $task.GetAwaiter().GetResult()
    $reader = New-Object Windows.Storage.Streams.DataReader ($stream.GetInputStreamAt(0))
    try {
        $null = Wait-Operation ($reader.LoadAsync([uint32]$stream.Size)) ([uint32])
        $bytes = New-Object byte[] ([int]$stream.Size)
        $reader.ReadBytes($bytes)
        [System.IO.File]::WriteAllBytes([System.IO.Path]::GetFullPath($OutputPng),$bytes)
    } finally { $reader.Dispose() }
    Write-Output ('Valid PDF rendered. Pages: ' + $document.PageCount + '; size: ' + $page.Size.Width + ' x ' + $page.Size.Height)
} finally { $page.Dispose(); $stream.Dispose() }
