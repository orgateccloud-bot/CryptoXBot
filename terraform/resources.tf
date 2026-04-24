# Artifact Registry para imagens Docker
resource "google_artifact_registry_repository" "botbinance" {
  location      = var.region
  repository_id = "botbinance"
  description   = "Docker repository for BotBinance HFT"
  format        = "DOCKER"
}

# Service Account para GitHub Actions
resource "google_service_account" "github_actions" {
  account_id   = "github-actions-deployer"
  display_name = "GitHub Actions Deployer"
  description  = "Service account for GitHub Actions CI/CD"
}

# IAM bindings para a service account
resource "google_project_iam_member" "github_actions_artifact_registry" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_project_iam_member" "github_actions_compute" {
  project = var.project_id
  role    = "roles/compute.instanceAdmin.v1"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_project_iam_member" "github_actions_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

# Workload Identity Pool para GitHub Actions
resource "google_iam_workload_identity_pool" "github_actions" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions Pool"
  description               = "Identity pool for GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github_actions" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_actions.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions Provider"
  attribute_mapping = {
    "google.subject" = "assertion.sub"
    "attribute.actor" = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# IAM binding para Workload Identity
resource "google_service_account_iam_member" "github_actions_workload_identity" {
  service_account_id = google_service_account.github_actions.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_actions.name}/attribute.repository/Veloso/BotBinance"
}

# Secrets no Secret Manager
resource "google_secret_manager_secret" "binance_api_key" {
  secret_id = "binance-api-key"
  replication {
    automatic = true
  }
}

resource "google_secret_manager_secret" "binance_api_secret" {
  secret_id = "binance-api-secret"
  replication {
    automatic = true
  }
}

# IAM para acesso aos secrets
resource "google_secret_manager_secret_iam_member" "binance_api_key_accessor" {
  secret_id = google_secret_manager_secret.binance_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_secret_manager_secret_iam_member" "binance_api_secret_accessor" {
  secret_id = google_secret_manager_secret.binance_api_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.github_actions.email}"
}

# Compute Engine VM para o bot
resource "google_compute_instance" "botbinance_vm" {
  name         = "botbinance-vm"
  machine_type = "e2-medium"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "cos-cloud/cos-stable"
    }
  }

  network_interface {
    network = "default"
    access_config {
      // Ephemeral IP
    }
  }

  service_account {
    email  = google_service_account.github_actions.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    gce-container-declaration = yamlencode({
      spec = {
        containers = [{
          name  = "botbinance"
          image = "${var.region}-docker.pkg.dev/${var.project_id}/botbinance/botbinance:latest"
          env = [
            { name = "ENV", value = "production" },
            { name = "GCP_PROJECT_ID", value = var.project_id },
            { name = "LOG_LEVEL", value = "INFO" },
            { name = "DRY_RUN", value = "false" }
          ]
          ports = [{
            containerPort = 8000
          }]
          resources = {
            limits = {
              cpu    = "1000m"
              memory = "1Gi"
            }
          }
        }]
        restartPolicy = "Always"
      }
    })
  }

  tags = ["botbinance", "hft", "production"]
}

# Firewall rule para acesso às métricas
resource "google_compute_firewall" "botbinance_metrics" {
  name    = "botbinance-metrics"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }

  source_ranges = ["0.0.0.0/0"] # TODO: Restringir para IPs específicos
  target_tags   = ["botbinance"]
}

# Bucket para Terraform state
resource "google_storage_bucket" "tf_state" {
  name          = "botbinance-tf-state"
  location      = var.region
  force_destroy = false

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}