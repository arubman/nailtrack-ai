# Create or update the NailTrack API on Azure Container Apps.
# Idempotent. Windows PowerShell 5.1. Does not print secrets.
#
#   az login
#   .\infra\containerapps-setup.ps1
#
# Defaults match the existing rg-nailtrack resources. Override any parameter
# on the command line. After it prints the client id and FQDN, set the GitHub
# secrets and variables listed at the end, then push to main.

param(
    [string]$ResourceGroup = "rg-nailtrack",
    [string]$Location = "westus",
    [string]$AcrName = "nailtrackacr91kia0",
    [string]$StorageAccountName = "nailtrackst91kia0",
    [string]$BlobContainer = "nailtrack-images",
    [string]$StaticWebAppOrigin = "https://zealous-beach-08fa8781e.6.azurestaticapps.net",
    [string]$EnvironmentName = "cae-nailtrack",
    [string]$ContainerAppName = "nailtrack-api",
    [string]$FileShareName = "nailtrack-data",
    [string]$GithubRepo = "arubman/nailtrack-ai",
    [string]$GithubBranch = "main",
    [string]$ImageRepository = "nailtrack-api"
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Invoke-Az {
    param(
        [Parameter(Mandatory = $true)][string[]]$AzArgs,
        [switch]$Secret
    )
    & az @AzArgs
    if ($LASTEXITCODE -ne 0) {
        if ($Secret) {
            throw "az command failed with exit code $LASTEXITCODE. Arguments were not printed because they include a secret."
        }
        throw "az $($AzArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-AzValue {
    param(
        [Parameter(Mandatory = $true)][string[]]$AzArgs,
        [switch]$Secret
    )
    $value = & az @AzArgs
    if ($LASTEXITCODE -ne 0) {
        if ($Secret) {
            throw "az command failed with exit code $LASTEXITCODE. Arguments were not printed because they include a secret."
        }
        throw "az $($AzArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
    if ($null -eq $value) {
        return ""
    }
    if ($value -is [System.Array]) {
        return ($value -join "`n").Trim()
    }
    return ([string]$value).Trim()
}

function Ensure-RoleAssignment {
    param(
        [Parameter(Mandatory = $true)][string]$PrincipalId,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Scope
    )
    $existing = Get-AzValue @(
        "role", "assignment", "list",
        "--assignee-object-id", $PrincipalId,
        "--assignee-principal-type", "ServicePrincipal",
        "--role", $Role,
        "--scope", $Scope,
        "--query", "[0].id",
        "-o", "tsv"
    )
    if ($existing) {
        Write-Host "Role $Role already assigned."
        return
    }
    $attempt = 0
    while ($attempt -lt 6) {
        & az role assignment create --assignee-object-id $PrincipalId --assignee-principal-type ServicePrincipal --role $Role --scope $Scope -o none
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Assigned $Role."
            return
        }
        $attempt = $attempt + 1
        Write-Host "Waiting for directory replication before assigning $Role ($attempt/6)."
        Start-Sleep -Seconds 10
    }
    throw "Could not assign $Role."
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )
    $encoding = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

$accountJson = Get-AzValue @("account", "show", "-o", "json")
if (-not $accountJson) {
    throw "az login is required before this script."
}
$account = $accountJson | ConvertFrom-Json
$subscriptionId = [string]$account.id
$tenantId = [string]$account.tenantId
Write-Host "Subscription: $subscriptionId"
Write-Host "Tenant: $tenantId"

Invoke-Az @("group", "create", "--name", $ResourceGroup, "--location", $Location, "-o", "none")

$storageKey = Get-AzValue -Secret @(
    "storage", "account", "keys", "list",
    "--resource-group", $ResourceGroup,
    "--account-name", $StorageAccountName,
    "--query", "[0].value",
    "-o", "tsv"
)
if (-not $storageKey) {
    throw "No storage account key was returned for $StorageAccountName."
}

Invoke-Az -Secret @(
    "storage", "share", "create",
    "--name", $FileShareName,
    "--account-name", $StorageAccountName,
    "--account-key", $storageKey,
    "--quota", "5",
    "-o", "none"
)
Write-Host "Azure Files share $FileShareName is ready."

Invoke-Az -Secret @(
    "storage", "container", "create",
    "--name", $BlobContainer,
    "--account-name", $StorageAccountName,
    "--account-key", $storageKey,
    "-o", "none"
)
Write-Host "Blob container $BlobContainer is private and ready."

$corsList = Get-AzValue -Secret @(
    "storage", "cors", "list",
    "--services", "b",
    "--account-name", $StorageAccountName,
    "--account-key", $storageKey,
    "-o", "json"
)
if ($corsList -notlike "*$StaticWebAppOrigin*") {
    Invoke-Az -Secret @(
        "storage", "cors", "add",
        "--services", "b",
        "--methods", "GET", "HEAD",
        "--origins", $StaticWebAppOrigin,
        "--allowed-headers", "*",
        "--exposed-headers", "*",
        "--max-age", "3600",
        "--account-name", $StorageAccountName,
        "--account-key", $storageKey,
        "-o", "none"
    )
    Write-Host "Storage CORS allows the Static Web App origin."
}
else {
    Write-Host "Storage CORS already allows the Static Web App origin."
}

$environmentId = ""
$showEnv = & az containerapp env show --name $EnvironmentName --resource-group $ResourceGroup --query id -o tsv 2>$null
if ($LASTEXITCODE -eq 0) {
    $environmentId = ([string]$showEnv).Trim()
}
if (-not $environmentId) {
    Write-Host "Creating Container Apps environment $EnvironmentName (this can take several minutes)."
    Invoke-Az @(
        "containerapp", "env", "create",
        "--name", $EnvironmentName,
        "--resource-group", $ResourceGroup,
        "--location", $Location,
        "-o", "none"
    )
    $environmentId = Get-AzValue @(
        "containerapp", "env", "show",
        "--name", $EnvironmentName,
        "--resource-group", $ResourceGroup,
        "--query", "id",
        "-o", "tsv"
    )
}
Write-Host "Environment: $environmentId"

# The account key is passed to Azure here and is not written to the host.
Invoke-Az -Secret @(
    "containerapp", "env", "storage", "set",
    "--name", $EnvironmentName,
    "--resource-group", $ResourceGroup,
    "--storage-name", $FileShareName,
    "--azure-file-account-name", $StorageAccountName,
    "--azure-file-account-key", $storageKey,
    "--azure-file-share-name", $FileShareName,
    "--access-mode", "ReadWrite",
    "-o", "none"
)
Write-Host "Environment storage $FileShareName is mounted with access mode ReadWrite."

$storageKey = $null

$acrImage = "${AcrName}.azurecr.io/${ImageRepository}:latest"
$image = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
& az acr repository show --name $AcrName --image "${ImageRepository}:latest" -o none 2>$null
if ($LASTEXITCODE -eq 0) {
    $image = $acrImage
    Write-Host "ACR already has ${ImageRepository}:latest. The container app will use it."
}
else {
    Write-Host "ACR has no ${ImageRepository}:latest yet. Bootstrapping from the public placeholder image."
    Write-Host "The placeholder does not serve port 8000. The app becomes healthy after GitHub Actions pushes the real image."
}

$yamlPath = Join-Path $env:TEMP "nailtrack-containerapp.yaml"
$yaml = @"
identity:
  type: SystemAssigned
properties:
  managedEnvironmentId: $environmentId
  configuration:
    ingress:
      external: true
      targetPort: 8000
      transport: auto
  template:
    containers:
      - name: $ContainerAppName
        image: "$image"
        resources:
          cpu: 1.0
          memory: 2.0Gi
        env:
          - name: PORT
            value: "8000"
          - name: WEB_CONCURRENCY
            value: "1"
          - name: ENVIRONMENT
            value: "development"
          - name: AUTH_MODE
            value: "dev"
          - name: DATABASE_URL
            value: "sqlite:////data/nailtrack.db"
          - name: AUTO_CREATE_TABLES
            value: "true"
          - name: STORAGE_BACKEND
            value: "azure"
          - name: AZURE_STORAGE_USE_MANAGED_IDENTITY
            value: "true"
          - name: AZURE_STORAGE_ACCOUNT_NAME
            value: "$StorageAccountName"
          - name: AZURE_STORAGE_CONTAINER
            value: "$BlobContainer"
          - name: CORS_ORIGINS
            value: "$StaticWebAppOrigin"
        volumeMounts:
          - volumeName: $FileShareName
            mountPath: /data
        probes:
          - type: Liveness
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 20
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 5
          - type: Readiness
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 15
            timeoutSeconds: 5
            failureThreshold: 10
    scale:
      minReplicas: 1
      maxReplicas: 1
    volumes:
      - name: $FileShareName
        storageType: AzureFile
        storageName: $FileShareName
        mountOptions: nobrl,dir_mode=0777,file_mode=0666
"@
Write-Utf8NoBom -Path $yamlPath -Text $yaml

$existingApp = & az containerapp show --name $ContainerAppName --resource-group $ResourceGroup --query name -o tsv 2>$null
if ($LASTEXITCODE -eq 0 -and ([string]$existingApp).Trim()) {
    Write-Host "Updating container app $ContainerAppName."
    Invoke-Az @("containerapp", "update", "--name", $ContainerAppName, "--resource-group", $ResourceGroup, "--yaml", $yamlPath, "-o", "none")
}
else {
    Write-Host "Creating container app $ContainerAppName."
    Invoke-Az @("containerapp", "create", "--name", $ContainerAppName, "--resource-group", $ResourceGroup, "--yaml", $yamlPath, "-o", "none")
}
Remove-Item -Path $yamlPath -Force

$principalId = Get-AzValue @(
    "containerapp", "identity", "assign",
    "--name", $ContainerAppName,
    "--resource-group", $ResourceGroup,
    "--system-assigned",
    "--query", "principalId",
    "-o", "tsv"
)
if (-not $principalId) {
    throw "The container app system identity has no principal id."
}
Write-Host "System identity principal id: $principalId"

$acrId = Get-AzValue @("acr", "show", "--name", $AcrName, "--query", "id", "-o", "tsv")
$storageId = Get-AzValue @(
    "storage", "account", "show",
    "--name", $StorageAccountName,
    "--resource-group", $ResourceGroup,
    "--query", "id",
    "-o", "tsv"
)
$groupId = Get-AzValue @("group", "show", "--name", $ResourceGroup, "--query", "id", "-o", "tsv")

Ensure-RoleAssignment -PrincipalId $principalId -Role "AcrPull" -Scope $acrId
Ensure-RoleAssignment -PrincipalId $principalId -Role "Storage Blob Data Contributor" -Scope $storageId
Ensure-RoleAssignment -PrincipalId $principalId -Role "Storage Blob Delegator" -Scope $storageId

Invoke-Az @(
    "containerapp", "registry", "set",
    "--name", $ContainerAppName,
    "--resource-group", $ResourceGroup,
    "--server", "$AcrName.azurecr.io",
    "--identity", "system",
    "-o", "none"
)
Write-Host "Container app pulls from $AcrName.azurecr.io with its system identity."

$deployAppName = "nailtrack-github-deploy"
$clientId = Get-AzValue @(
    "ad", "app", "list",
    "--display-name", $deployAppName,
    "--query", "[0].appId",
    "-o", "tsv"
)
if (-not $clientId) {
    $clientId = Get-AzValue @(
        "ad", "app", "create",
        "--display-name", $deployAppName,
        "--query", "appId",
        "-o", "tsv"
    )
    Write-Host "Created Entra app $deployAppName."
}
else {
    Write-Host "Entra app $deployAppName already exists."
}

$servicePrincipalId = Get-AzValue @(
    "ad", "sp", "list",
    "--filter", "appId eq '$clientId'",
    "--query", "[0].id",
    "-o", "tsv"
)
if (-not $servicePrincipalId) {
    $servicePrincipalId = Get-AzValue @(
        "ad", "sp", "create",
        "--id", $clientId,
        "--query", "id",
        "-o", "tsv"
    )
    Write-Host "Created the GitHub deploy service principal."
}

$subject = "repo:${GithubRepo}:ref:refs/heads/${GithubBranch}"
$federated = Get-AzValue @("ad", "app", "federated-credential", "list", "--id", $clientId, "-o", "json")
if ($federated -notlike "*$subject*") {
    $credentialPath = Join-Path $env:TEMP "nailtrack-github-federated.json"
    $credential = @"
{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "description": "GitHub Actions deploy from $GithubBranch",
  "audiences": ["api://AzureADTokenExchange"]
}
"@
    Write-Utf8NoBom -Path $credentialPath -Text $credential
    Invoke-Az @("ad", "app", "federated-credential", "create", "--id", $clientId, "--parameters", $credentialPath, "-o", "none")
    Remove-Item -Path $credentialPath -Force
    Write-Host "Federated credential subject: $subject"
}
else {
    Write-Host "Federated credential already allows $subject."
}

Ensure-RoleAssignment -PrincipalId $servicePrincipalId -Role "Contributor" -Scope $groupId

$fqdn = Get-AzValue @(
    "containerapp", "show",
    "--name", $ContainerAppName,
    "--resource-group", $ResourceGroup,
    "--query", "properties.configuration.ingress.fqdn",
    "-o", "tsv"
)

Write-Host ""
Write-Host "GitHub OIDC client id: $clientId"
Write-Host "Ingress FQDN: $fqdn"
Write-Host ""
Write-Host "Set these GitHub Actions secrets:"
Write-Host "  AZURE_CLIENT_ID=$clientId"
Write-Host "  AZURE_TENANT_ID=$tenantId"
Write-Host "  AZURE_SUBSCRIPTION_ID=$subscriptionId"
Write-Host "  AZURE_STATIC_WEB_APPS_API_TOKEN from the Static Web App deployment token"
Write-Host "Set these GitHub Actions variables:"
Write-Host "  AZURE_RESOURCE_GROUP=$ResourceGroup"
Write-Host "  AZURE_ACR_NAME=$AcrName"
Write-Host "  AZURE_CONTAINERAPP_NAME=$ContainerAppName"
Write-Host "  VITE_API_BASE_URL=https://$fqdn"
Write-Host "  VITE_AUTH_MODE=dev"
Write-Host "Role changes can take a few minutes before the first image pull succeeds."
