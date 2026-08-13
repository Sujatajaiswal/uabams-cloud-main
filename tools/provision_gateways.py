import os
import sys
import argparse
import asyncio
import asyncpg
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=env_path, override=True)

PG_URL = os.getenv("DATABASE_URL")

async def provision_gateways(gateway_list: list[str]):
    if not PG_URL:
        print("DATABASE_URL not set in environment!")
        return

    print(f"Connecting to PostgreSQL database...")
    pool = await asyncpg.create_pool(PG_URL)

    async with pool.acquire() as conn:
        for gid in gateway_list:
            gid = gid.strip()
            if not gid:
                continue
            
            await conn.execute("""
                INSERT INTO gateways (gateway_id, train_id, status, provision_status)
                VALUES ($1, 'UNASSIGNED', 'active', 'provisioned')
                ON CONFLICT (gateway_id) DO UPDATE 
                SET provision_status = 'provisioned', status = 'active';
            """, gid)
            print(f"Provisioned gateway [{gid}] in PostgreSQL database.")

    await pool.close()
    print(f"\nBulk Provisioning Complete for {len(gateway_list)} gateways!")

def main():
    parser = argparse.ArgumentParser(description="Bulk Provision Gateway IDs into PostgreSQL")
    parser.add_argument("--gateways", type=str, default="GW_UABAMS_BOGIE_01,GW_UABAMS_BOGIE_02", help="Comma separated gateway IDs")
    args = parser.parse_args()

    gateway_list = [g.strip() for g in args.gateways.split(",") if g.strip()]
    asyncio.run(provision_gateways(gateway_list))

if __name__ == "__main__":
    main()
