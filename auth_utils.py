# auth_utils.py - FIXED VERSION
from supabase import create_client
import os

# YOUR ACTUAL SUPABASE CREDENTIALS
SUPABASE_URL = "https://qmlmexokphzqrinbdfwk.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFtbG1leG9rcGh6cXJpbmJkZndrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjU2NDg5ODksImV4cCI6MjA4MTIyNDk4OX0.n4SY5_s-VSr9BHudSUhgJRc90wMcIJkP75UTJcX76Qo"

# Initialize Supabase
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def is_email_allowed(email: str) -> bool:
    """Check if email is in allowed_users table - MAX 2 USERS - FIXED"""
    try:
        print(f"🔐 Checking email: {email}")
        
        # First check if email already exists
        response = supabase.table("allowed_users") \
            .select("email") \
            .eq("email", email) \
            .execute()
        
        if len(response.data) > 0:
            print(f"✅ Email {email} found in allowed_users")
            return True  # User already exists
        
        # Check how many users are already in the table
        count_response = supabase.table("allowed_users") \
            .select("*") \
            .execute()
        
        current_count = len(count_response.data)
        print(f"📊 Current user count in DB: {current_count}")
        
        # If less than 2 users, add this one
        if current_count < 2:
            print(f"➕ Adding new user: {email}")
            insert_response = supabase.table("allowed_users").insert({"email": email}).execute()
            print(f"✅ Insert successful for: {email}")
            return True
        else:
            print(f"❌ Already have 2 users, denying {email}")
            # Show who the current users are
            users_response = supabase.table("allowed_users") \
                .select("email") \
                .execute()
            current_users = [user["email"] for user in users_response.data]
            print(f"👥 Current allowed users: {current_users}")
            return False
            
    except Exception as e:
        print(f"❌ Auth error: {e}")
        return False