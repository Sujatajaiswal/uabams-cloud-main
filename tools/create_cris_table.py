import asyncio
import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS cris_exports (
    id                 SERIAL PRIMARY KEY,
    archive_sha256     VARCHAR(64)  NOT NULL,
    gateway_id         VARCHAR(100) NOT NULL,
    export_type        VARCHAR(20)  NOT NULL,   -- SPATIAL | PROCESSED | ALERTS
    train_id           VARCHAR(50),
    logical_gateway_id VARCHAR(100),
    session_name       VARCHAR(100),
    -- Generated file metadata
    file_name          VARCHAR(255),
    file_path          TEXT,
    file_size          BIGINT,
    file_sha256        VARCHAR(64),
    record_count       INTEGER,
    -- Lifecycle status: PENDING → GENERATED → UPLOADING → UPLOADED | FAILED
    status             VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    transfer_attempts  INTEGER      NOT NULL DEFAULT 0,
    last_error         TEXT,
    -- Timestamps
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    generated_at       TIMESTAMP WITH TIME ZONE,
    uploaded_at        TIMESTAMP WITH TIME ZONE,
    UNIQUE (archive_sha256, gateway_id, export_type)
);

CREATE INDEX IF NOT EXISTS idx_cris_exports_archive_sha256
    ON cris_exports(archive_sha256);

CREATE INDEX IF NOT EXISTS idx_cris_exports_status
    ON cris_exports(status);

CREATE INDEX IF NOT EXISTS idx_cris_exports_export_type
    ON cris_exports(export_type);
"""

async def main():
    conn_str = "postgresql://uabams_user:uabams_pass@20.8.1.15:5433/uabams_db"
    print(f"Connecting to {conn_str}...")
    conn = await asyncpg.connect(conn_str)
    print("Executing DDL...")
    await conn.execute(DDL)
    print("DDL executed successfully!")
    
    # Verify table existence
    cols = await conn.fetch("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'cris_exports'
        ORDER BY ordinal_position;
    """)
    print("\nColumns in cris_exports:")
    for col in cols:
        print(f" - {col['column_name']}: {col['data_type']} (nullable={col['is_nullable']})")
        
    await conn.close()
    print("\nConnection closed.")

if __name__ == "__main__":
    asyncio.run(main())
