"""CSV export via DuckDB SQL COPY."""

import logging

logger = logging.getLogger(__name__)

TABLE_NAME = "triplets"


def export_to_csv(self, path, table_name=TABLE_NAME):
    """Export triplets table to CSV file via SQL COPY."""
    self.execute(f"COPY {table_name} TO '{path}' (HEADER, DELIMITER ',')")
    logger.info(f"Exported {table_name} to {path}")
