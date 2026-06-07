output "database_name" {
  description = "Glue Data Catalog database name."
  value       = aws_glue_catalog_database.this.name
}

output "table_name" {
  description = "Glue table name (database-qualified as <database_name>.<table_name>)."
  value       = aws_glue_catalog_table.this.name
}
