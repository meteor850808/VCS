# -*- coding: utf-8 -*-
"""Delete the latest failed GDB archive record."""
import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def cleanup():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(
            "DELETE FROM point_features WHERE stored_file_id IN "
            "(SELECT id FROM stored_files WHERE source_archive LIKE '%.gdb.zip')"
        ))
        print(f"Deleted {r.rowcount} point_features")
        r = await db.execute(text(
            "DELETE FROM polygon_features WHERE stored_file_id IN "
            "(SELECT id FROM stored_files WHERE source_archive LIKE '%.gdb.zip')"
        ))
        print(f"Deleted {r.rowcount} polygon_features")
        r = await db.execute(text(
            "DELETE FROM line_features WHERE stored_file_id IN "
            "(SELECT id FROM stored_files WHERE source_archive LIKE '%.gdb.zip')"
        ))
        print(f"Deleted {r.rowcount} line_features")
        r = await db.execute(text(
            "DELETE FROM stored_files WHERE original_name LIKE '%.gdb.zip' OR source_archive LIKE '%.gdb.zip'"
        ))
        print(f"Deleted {r.rowcount} stored_files")
        await db.commit()
        print("Done")

asyncio.run(cleanup())
