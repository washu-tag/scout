terraform {
  backend "s3" {
    region = "us-east-1"
    bucket = # Complete with S3 bucket's name
    key    = "terraform.tfstate"
  }
}