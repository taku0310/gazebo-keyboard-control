<#
.SYNOPSIS
    Launch the robot simulation stack and the keyboard controller on Windows.

.DESCRIPTION
    Starts the backing services (ros_master, control_logic, gazebo) detached,
    waits for ROS Master, then runs the keyboard controller interactively.

    The keyboard controller is launched with `docker compose run` rather than
    `exec`: the controller defaults to stdin input mode when a TTY is
    attached, which is the only reliable live-input path inside a container
    (pynput needs a display backend the container does not have). `run`
    gives a fresh interactive instance with the requested args.

.PARAMETER Scenario
    Scenario JSON to load (host path under scenarios\). Optional.

.PARAMETER Auto
    Auto-play the scenario on start, then exit.

.PARAMETER Log
    Tee all output to the given log file as well as the console.

.EXAMPLE
    .\run_keyboard.ps1
    .\run_keyboard.ps1 -Scenario "scenarios/demo_scenario_01.json"
    .\run_keyboard.ps1 -Scenario "scenarios/demo_scenario_01.json" -Auto
#>

param(
    [string]$Scenario = "",
    [switch]$Auto = $false,
    [string]$Log = ""
)

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------- #
# Work from the script's own directory.
# --------------------------------------------------------------------------- #
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Optional transcript logging.
if ($Log -ne "") {
    Start-Transcript -Path $Log -Append | Out-Null
    Write-Host "📝 Logging to $Log"
}

# --------------------------------------------------------------------------- #
# Pick a docker compose command (v2 plugin vs legacy v1).
# --------------------------------------------------------------------------- #
function Get-ComposeCommand {
    docker compose version *> $null
    if ($LASTEXITCODE -eq 0) {
        return @("docker", "compose")
    }
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        return @("docker-compose")
    }
    return $null
}

$Compose = Get-ComposeCommand
if ($null -eq $Compose) {
    Write-Host "❌ Docker Compose not found. Install Docker Desktop." -ForegroundColor Red
    if ($Log -ne "") { Stop-Transcript | Out-Null }
    exit 1
}

# Verify the Docker daemon is reachable.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker daemon is not running. Start Docker Desktop and retry." -ForegroundColor Red
    if ($Log -ne "") { Stop-Transcript | Out-Null }
    exit 1
}

Write-Host "🖥️  Platform: Windows"
Write-Host ("🐳 Using: " + ($Compose -join " "))

# Helper to invoke compose with arguments. Handles both the two-element
# ("docker","compose") and single-element ("docker-compose") forms; a naive
# $Compose[1..($Compose.Count-1)] slice would misbehave for the single case
# because PowerShell's 1..0 range counts down to 0.
function Invoke-Compose {
    param([string[]]$ComposeArgs)
    if ($Compose.Count -gt 1) {
        & $Compose[0] ($Compose[1..($Compose.Count - 1)] + $ComposeArgs)
    }
    else {
        & $Compose[0] $ComposeArgs
    }
}

# --------------------------------------------------------------------------- #
# Cleanup helper.
# --------------------------------------------------------------------------- #
function Stop-Stack {
    Write-Host ""
    Write-Host "🧹 Stopping all containers..."
    Invoke-Compose @("down", "--remove-orphans")
    Write-Host "✅ Done."
    if ($Log -ne "") { Stop-Transcript | Out-Null }
}

try {
    # ----------------------------------------------------------------------- #
    # Start backing services detached. keyboard_controller is a TCP client of
    # ros_bridge (the DDS publisher of /cmd_vel). ROS 2 is masterless.
    # ----------------------------------------------------------------------- #
    Write-Host "🚀 Starting backing services (ros_bridge, control_logic, gazebo)..."
    Invoke-Compose @("up", "-d", "ros_bridge", "control_logic", "gazebo")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start services."
    }

    # Give the services a moment to come up and discover over DDS.
    Write-Host "⏳ Waiting for services (ROS 2 DDS discovery + bridge TCP)..."
    Start-Sleep -Seconds 3
    Write-Host "✅ Services should be up."

    # ----------------------------------------------------------------------- #
    # Build the keyboard controller command.
    # ----------------------------------------------------------------------- #
    $CtrlCmd = @("python3", "/app/src/keyboard_input_controller.py")

    if ($Scenario -ne "") {
        if (-not (Test-Path $Scenario)) {
            Write-Host "⚠️  Scenario file not found on host: $Scenario" -ForegroundColor Yellow
            Write-Host "   (continuing; it must exist under scenarios\ to be mounted)"
        }
        # scenarios\ is mounted at /app/scenarios inside the container.
        $ContainerScenario = "/app/scenarios/" + (Split-Path -Leaf $Scenario)
        $CtrlCmd += @("--scenario", $ContainerScenario)
        Write-Host "🎬 Scenario: $Scenario -> $ContainerScenario"
    }

    if ($Auto) {
        $CtrlCmd += "--auto"
        Write-Host "▶️  Auto-play enabled."
    }

    # ----------------------------------------------------------------------- #
    # Run the keyboard controller interactively.
    # ----------------------------------------------------------------------- #
    Write-Host "⌨️  Launching keyboard controller... (Ctrl+C to stop everything)"
    Write-Host "--------------------------------------------------------------"
    Invoke-Compose (@("run", "--rm", "keyboard_controller") + $CtrlCmd)
}
catch {
    Write-Host "❌ Error: $_" -ForegroundColor Red
}
finally {
    Stop-Stack
}
