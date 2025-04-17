# Se crea vpc
resource "aws_vpc" "my_vpc" {
  cidr_block = var.vpc_cidr
  
  tags = var.tags
}

resource "aws_internet_gateway" "my_igw" {
  vpc_id = aws_vpc.my_vpc.id

  tags = var.tags
}

resource "aws_subnet" "my_private_subnets" {
  count = length(var.vpc_private_subnets)

  vpc_id = aws_vpc.my_vpc.id
  cidr_block = var.vpc_private_subnets[count.index]
  availability_zone = var.availability_zones[count.index]
  
  tags = merge(var.tags, {"kubernetes.io/role/internal-elb": 1})
}

resource "aws_subnet" "my_public_subnets" {
  count = length(var.vpc_public_subnets)
  
  vpc_id = aws_vpc.my_vpc.id
  cidr_block = var.vpc_public_subnets[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = merge(var.tags, {"kubernetes.io/role/elb": 1})
}

resource "aws_eip" "my_nat" {
  tags = var.tags
}

resource "aws_nat_gateway" "my_nat_gateway" {
  allocation_id = aws_eip.my_nat.id
  subnet_id = aws_subnet.my_public_subnets[0].id
  
  tags = var.tags

  depends_on = [ 
    aws_internet_gateway.my_igw 
  ]
}

resource "aws_route_table" "my_route_table_private" {
  vpc_id = aws_vpc.my_vpc.id
  route {
    cidr_block = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.my_nat_gateway.id
  }

  tags = var.tags
}

resource "aws_route_table" "my_route_table_public" {
  vpc_id = aws_vpc.my_vpc.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.my_igw.id
  }

  tags = var.tags 
}

resource "aws_route_table_association" "my_route_table_private_az1" {
  subnet_id = aws_subnet.my_private_subnets[0].id
  route_table_id = aws_route_table.my_route_table_private.id
}

resource "aws_route_table_association" "my_route_table_private_az2" {
  subnet_id = aws_subnet.my_private_subnets[1].id
  route_table_id = aws_route_table.my_route_table_private.id
}

resource "aws_route_table_association" "my_route_table_public_az1" {
  subnet_id = aws_subnet.my_public_subnets[0].id
  route_table_id = aws_route_table.my_route_table_public.id
}

resource "aws_route_table_association" "my_route_table_public_az2" {
  subnet_id = aws_subnet.my_public_subnets[0].id
  route_table_id = aws_route_table.my_route_table_public.id
}