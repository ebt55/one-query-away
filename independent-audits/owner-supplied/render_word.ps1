param([Parameter(Mandatory=$true)][string]$Source, [Parameter(Mandatory=$true)][string]$Pdf)
$ErrorActionPreference = 'Stop'
$sourceResolved = (Resolve-Path -LiteralPath $Source).Path
$pdfResolved = [System.IO.Path]::GetFullPath($Pdf)
$workspace = [System.IO.Path]::GetFullPath('C:/Users/ebin/codex-ground/swarm-allignment-research-sprint-nov-2026')
if (-not $pdfResolved.StartsWith($workspace + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'PDF output must stay inside the task workspace.' }
if (Test-Path -LiteralPath $pdfResolved) { throw 'Refusing to replace an existing PDF.' }
$before = (Get-FileHash -LiteralPath $sourceResolved -Algorithm SHA256).Hash
$word = $null
$doc = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $word.AutomationSecurity = 3
  $oldLinks = $word.Options.UpdateLinksAtOpen
  $word.Options.UpdateLinksAtOpen = $false
  $doc = $word.Documents.Open($sourceResolved, $false, $true, $false)
  $doc.Repaginate()
  $null = $doc.Fields.Update()
  $doc.ExportAsFixedFormat($pdfResolved, 17, $false)
  $pages = $doc.ComputeStatistics(2)
  $doc.Close(0)
  [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) | Out-Null
  $doc = $null
  $word.Options.UpdateLinksAtOpen = $oldLinks
  if ((Get-FileHash -LiteralPath $sourceResolved -Algorithm SHA256).Hash -ne $before) { throw 'Read-only reference changed.' }
  [pscustomobject]@{ source=$sourceResolved; source_sha256=$before.ToLower(); pdf=$pdfResolved; pages=$pages; source_unchanged=$true; renderer='Microsoft Word read-only PDF export' } | ConvertTo-Json
} finally {
  if ($null -ne $doc) { $doc.Close(0); [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) | Out-Null }
  if ($null -ne $word) { $word.Quit(0); [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) | Out-Null }
}
