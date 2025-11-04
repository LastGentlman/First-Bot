from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL= os.getenv("SUPABASE_URL")
SUPABASE_KEY=os.getenv("SUPABASE_KEY")
BOT_TOKEN=os.getenv("BOT_TOKEN")