variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "patchpilot"
}

variable "ttl_minutes" {
  description = "Every instance shuts itself down (and terminates) this long after first boot. Survives reboots."
  type        = number
  default     = 240
}

variable "use_spot" {
  description = "One-time Spot instances. Fine for a lab; an interruption just ends the session early."
  type        = bool
  default     = true
}

variable "enable_windows" {
  description = "Windows Server app node (IIS). The most expensive node; turn off to save money."
  type        = bool
  default     = true
}

variable "enable_db" {
  description = "RHEL database node running Oracle Database Free in Podman."
  type        = bool
  default     = true
}

variable "master_type" {
  type    = string
  default = "t3.medium"
}

variable "app_type" {
  type    = string
  default = "t3.small"
}

variable "db_type" {
  description = "Oracle reserves ~2 GB at start; with a STIG scan on top, 4 GB froze the host. 8 GB has room."
  type        = string
  default     = "t3.large"
}

variable "win_type" {
  type    = string
  default = "t3.medium"
}

variable "budget_email" {
  description = "If set, creates a $20/month AWS Budget that emails at 80% and 100%."
  type        = string
  default     = ""
}
