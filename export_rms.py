import asyncio
import asyncpg
import csv

DATABASE_URL = "postgresql://uabams_user:uabams_pass@localhost:5432/uabams_db"
session_name = "SESSION_20260804_081120976"

async def extract():
    try:
        conn = await asyncpg.connect(DATABASE_URL)

        sql = """
        SELECT 
            gateway_id, 
            created_at, 
            al_x_g, al_y_g, al_z_g, 
            ar_x_g, ar_y_g, ar_z_g, 
            bg_x_g, bg_y_g, bg_z_g, 
            speed, position_mm 
        FROM rms_records 
        WHERE session_name = $1 
        ORDER BY created_at ASC
        """
        
        # asyncpg fetch() returns Record objects
        rows = await conn.fetch(sql, session_name)
        
        if not rows:
            print(f"No records found for session {session_name}")
        else:
            # keys are from the first record
            colnames = list(rows[0].keys())
            filename = f"{session_name}_rms.csv"
            with open(filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(colnames)
                for row in rows:
                    writer.writerow(list(row.values()))
            print(f"Exported {len(rows)} records to {filename}")
            
        await conn.close()
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(extract())
