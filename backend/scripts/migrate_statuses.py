import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient

SPANISH_TO_ENGLISH_STATUS = {
    "nuevo": "new",
    "calificando": "contacted",
    "calificado": "qualified",
    "propuesta_solicitada": "proposal",
    "propuesta_enviada": "proposal",
    "negociacion": "proposal",
    "ganado": "won",
    "perdido": "lost",
    "no_responde": "lost"
}

async def main():
    uri = (os.environ.get("MONGO_URL") or "").strip()
    if not uri:
        raise RuntimeError("MONGO_URL no está configurado")
    client = AsyncIOMotorClient(uri)
    
    for db_name in ["Latus", "Latus_CRM"]:
        db = client[db_name]
        print(f"Checking database: {db_name}")
        
        # Find all leads
        leads = await db.leads.find({}).to_list(1000)
        updated_count = 0
        for lead in leads:
            current_status = lead.get("status")
            if current_status in SPANISH_TO_ENGLISH_STATUS:
                target_status = SPANISH_TO_ENGLISH_STATUS[current_status]
                await db.leads.update_one(
                    {"id": lead["id"]},
                    {"$set": {"status": target_status}}
                )
                print(f" -> Updated lead {lead.get('id')} ({lead.get('title')}) from '{current_status}' to '{target_status}'")
                updated_count += 1
        print(f"Finished database {db_name}. Updated {updated_count} leads.\n")

if __name__ == "__main__":
    asyncio.run(main())
