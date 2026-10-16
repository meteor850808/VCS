"""Check if data_standards table has sheet_count column."""
import asyncio
from app.db.session import async_session_factory
from sqlalchemy import text

async def main():
    async with async_session_factory() as s:
        r = await s.execute(
            text("SELECT column_name, data_type FROM information_schema.columns "
                 "WHERE table_name='data_standards' ORDER BY ordinal_position")
        )
        for row in r:
            print(f"  {row[0]:25s} {row[1]}")

        # Also check if v1 endpoint works with raw SQL
        print("\n--- Testing v1 query directly ---")
        count = await s.execute(text("SELECT COUNT(*) FROM data_standards"))
        print(f"Total standards: {count.scalar()}")
        
        # Try the actual ORM query
        from sqlalchemy import select
        from app.models.data_standard import DataStandard
        result = await s.execute(
            select(DataStandard).order_by(DataStandard.created_at.desc())
        )
        rows = result.scalars().all()
        print(f"ORM query returned: {len(rows)} rows")
        if rows:
            std = rows[0]
            print(f"First std id={std.id}, name={std.name}")
            print(f"Has sheet_count: {hasattr(std, 'sheet_count')}")
            # Check if model attrs match DB columns
            print(f"Model columns: id={hasattr(std,'id')}, name={hasattr(std,'name')}, "
                  f"description={hasattr(std,'description')}, sheet_count={hasattr(std,'sheet_count')}")

if __name__ == "__main__":
    asyncio.run(main())
