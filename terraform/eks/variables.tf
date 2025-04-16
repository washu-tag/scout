variable "tags" {
	type = map(string)
}

variable "cluster_name" {
	type = string
}

variable "cluster_subnets" {
	type = map(list(string))
}

variable "cluster_role" {
	type = string
}

variable "cluster_nodes_role" {
	type = string
}
