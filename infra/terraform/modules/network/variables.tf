variable "name" {
  description = "Name prefix applied to all resources created by this module (e.g. \"agent-intel-dev\")."
  type        = string
}

variable "region" {
  description = "AWS region to deploy the network into."
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of Availability Zones to spread public/private subnets across."
  type        = number
  default     = 2
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for the public subnets, one per AZ. Must have at least az_count entries."
  type        = list(string)
  default     = ["10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for the private subnets, one per AZ. Must have at least az_count entries."
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24", "10.0.12.0/24"]
}

variable "single_nat_gateway" {
  description = "If true, create a single NAT Gateway shared by all private subnets (cheaper for dev). If false, one NAT Gateway per AZ (more resilient, for prod)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}
