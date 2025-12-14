# supabase_client.py
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# MAKE SURE THIS IS THE SAME URL
SUPABASE_URL = "https://qmlmexokphzqrinbdfwk.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFtbG1leG9rcGh6cXJpbmJkZndrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjU2NDg5ODksImV4cCI6MjA4MTIyNDk4OX0.n4SY5_s-VSr9BHudSUhgJRc90wMcIJkP75UTJcX76Qo"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)