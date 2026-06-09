"""N-Quads export via DuckDB SQL COPY."""

import logging

logger = logging.getLogger(__name__)

TABLE_NAME = "triplets"

CIM = "http://iec.ch/TC57/CIM100#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def export_to_nquads(self, path, table_name=TABLE_NAME):
    """Export triplets table to N-Quads file via SQL COPY."""
    self.execute(f"""
        COPY (
            SELECT
                '<urn:uuid:' || ID || '>' || ' ' ||
                CASE WHEN KEY = 'Type'
                    THEN '<{RDF_TYPE}>'
                    ELSE '<{CIM}' || KEY || '>'
                END || ' ' ||
                CASE WHEN KEY = 'Type'
                    THEN '<{CIM}' || VALUE || '>'
                    ELSE '"' || VALUE || '"'
                END || ' ' ||
                '<urn:uuid:' || INSTANCE_ID || '>' || ' .' as quad
            FROM {table_name}
        ) TO '{path}' (HEADER false, QUOTE '', DELIMITER '')
    """)
    logger.info(f"Exported N-Quads to {path}")
