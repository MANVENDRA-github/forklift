# sync.ps1 — push forklift's runtime command + subagent definitions to the global
# Claude Code config (~/.claude) so /contribute:solve works from ANY repo.
#
# Copies ONLY .claude/commands/ and .claude/agents/ (overwriting). It deliberately does
# NOT touch settings.json, hooks/, or soul.md — those are project-local or personal.

$ErrorActionPreference = "Stop"

$repo   = $PSScriptRoot
$target = Join-Path $env:USERPROFILE ".claude"

foreach ($dir in @("commands", "agents")) {
    $src = Join-Path $repo   ".claude\$dir"
    $dst = Join-Path $target $dir

    if (-not (Test-Path $src)) {
        Write-Host "skip   .claude/$dir (not present in repo)"
        continue
    }

    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -Path (Join-Path $src "*") -Destination $dst -Recurse -Force

    $files = Get-ChildItem -Path $src -Recurse -File
    Write-Host "synced .claude/$dir -> $dst ($($files.Count) file(s))"
    foreach ($f in $files) {
        Write-Host "         $($f.FullName.Substring($src.Length + 1))"
    }
}

Write-Host "done."
