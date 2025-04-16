output "vpc_id" {
  value = aws_vpc.my_vpc.id
}

output "subnets_ids" {
  value = {
    "private_subnets": [
      aws_subnet.my_private_subnets[0].id,
      aws_subnet.my_private_subnets[1].id,
    ]
    "public_subnets": [
    aws_subnet.my_public_subnets[0].id,
    aws_subnet.my_public_subnets[1].id,
    ]
  }
}
