# auth.py - FIXED VERSION
from supabase import create_client
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

# Configuration - USE ENVIRONMENT VARIABLES FOR SECURITY!
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qmlmexokphzqrinbdfwk.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")  # Use service role for backend

# Table names matching your database
PERMANENT_TABLE = "permanent_allowed_user"  # VIP users table
EXTRA_TABLE = "other_extra_user"  # Temporary users table
ACTIVITY_TABLE = "user_activity"  # Session tracking table

MAX_EXTRA_USERS = 500  # Max concurrent users as per your backend

# Initialize logger
logger = logging.getLogger("auth_utils")
logger.setLevel(logging.INFO)

# Initialize Supabase client
supabase = None
try:
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase client initialized successfully")
    else:
        logger.error("❌ Missing Supabase credentials")
except Exception as e:
    logger.error(f"❌ Failed to initialize Supabase: {e}")
    supabase = None

# ========== HELPER FUNCTIONS ==========
def _now_iso() -> str:
    """Get current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()

def _execute_query(query_builder):
    """Safely execute a Supabase query."""
    if not supabase:
        logger.error("Supabase client not initialized")
        return None
    try:
        return query_builder.execute()
    except Exception as e:
        logger.error(f"Query execution error: {e}")
        return None

def _get_count(result) -> int:
    """Extract count from Supabase result."""
    if not result or not hasattr(result, 'data'):
        return 0
    if hasattr(result, 'count') and result.count is not None:
        return int(result.count)
    return len(result.data) if result.data else 0

# ========== CORE AUTH FUNCTIONS ==========
def get_vip_user_count() -> int:
    """Get total number of VIP users in permanent table."""
    try:
        if not supabase:
            return 0
        result = supabase.table(PERMANENT_TABLE).select("email", count="exact").execute()
        return _get_count(result)
    except Exception as e:
        logger.error(f"get_vip_user_count error: {e}")
        return 0

def get_active_extra_users_count() -> int:
    """Count active extra users (not expired)."""
    try:
        if not supabase:
            return 0
        now = _now_iso()
        result = supabase.table(EXTRA_TABLE).select("email", count="exact").gt("expires_at", now).execute()
        return _get_count(result)
    except Exception as e:
        logger.error(f"get_active_extra_users_count error: {e}")
        return 0

def get_active_vip_sessions_count() -> int:
    """Count active VIP sessions."""
    try:
        if not supabase:
            return 0
        now = _now_iso()
        result = supabase.table(ACTIVITY_TABLE).select("email", count="exact") \
            .eq("role", "vip") \
            .eq("session_type", "login") \
            .gt("expires_at", now) \
            .execute()
        return _get_count(result)
    except Exception as e:
        logger.error(f"get_active_vip_sessions_count error: {e}")
        return 0

def is_vip_user(email: str) -> bool:
    """Check if email exists in VIP table."""
    try:
        if not supabase:
            return False
        email = email.strip().lower()
        result = supabase.table(PERMANENT_TABLE).select("email").eq("email", email).execute()
        return bool(result.data and len(result.data) > 0)
    except Exception as e:
        logger.error(f"is_vip_user error: {e}")
        return False

def has_active_extra_session(email: str) -> bool:
    """Check if user has active session in extra table."""
    try:
        if not supabase:
            return False
        email = email.strip().lower()
        now = _now_iso()
        result = supabase.table(EXTRA_TABLE).select("email") \
            .eq("email", email) \
            .gt("expires_at", now) \
            .execute()
        return bool(result.data and len(result.data) > 0)
    except Exception as e:
        logger.error(f"has_active_extra_session error: {e}")
        return False

def has_active_vip_session(email: str) -> bool:
    """Check if VIP user has active session."""
    try:
        if not supabase:
            return False
        email = email.strip().lower()
        now = _now_iso()
        result = supabase.table(ACTIVITY_TABLE).select("email") \
            .eq("email", email) \
            .eq("role", "vip") \
            .eq("session_type", "login") \
            .gt("expires_at", now) \
            .execute()
        return bool(result.data and len(result.data) > 0)
    except Exception as e:
        logger.error(f"has_active_vip_session error: {e}")
        return False

def create_extra_user_session(email: str, minutes: int = 2) -> Tuple[bool, str]:
    """Create session for extra user. Returns (success, message)."""
    try:
        if not supabase:
            return False, "Database not available"
        
        # Check if we can add more users
        active_count = get_active_extra_users_count()
        if active_count >= MAX_EXTRA_USERS:
            return False, f"Maximum users reached ({MAX_EXTRA_USERS}/{MAX_EXTRA_USERS})"
        
        email = email.strip().lower()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=minutes)
        
        # Check if already has active session
        if has_active_extra_session(email):
            return False, "User already has active session"
        
        session_data = {
            "email": email,
            "login_time": now.isoformat(),
            "expires_at": expires_at.isoformat()
        }
        
        result = supabase.table(EXTRA_TABLE).insert(session_data).execute()
        
        if result.data:
            logger.info(f"✅ Created extra session for {email}")
            return True, "Session created successfully"
        else:
            return False, "Failed to create session"
            
    except Exception as e:
        logger.error(f"create_extra_user_session error: {e}")
        return False, f"Error: {str(e)}"

def create_vip_session(email: str, minutes: int = 2) -> Tuple[bool, str]:
    """Create VIP session. Returns (success, message)."""
    try:
        if not supabase:
            return False, "Database not available"
        
        # Check if user is actually VIP
        if not is_vip_user(email):
            return False, "User is not VIP"
        
        email = email.strip().lower()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=minutes)
        
        session_data = {
            "email": email,
            "role": "vip",
            "session_type": "login",
            "login_time": now.isoformat(),
            "expires_at": expires_at.isoformat()
        }
        
        result = supabase.table(ACTIVITY_TABLE).insert(session_data).execute()
        
        if result.data:
            logger.info(f"✅ Created VIP session for {email}")
            return True, "VIP session created"
        else:
            return False, "Failed to create VIP session"
            
    except Exception as e:
        logger.error(f"create_vip_session error: {e}")
        return False, f"Error: {str(e)}"

def can_extra_user_login() -> bool:
    """Check if extra user can login (under max limit)."""
    try:
        count = get_active_extra_users_count()
        return count < MAX_EXTRA_USERS
    except Exception as e:
        logger.error(f"can_extra_user_login error: {e}")
        return False

def cleanup_expired_sessions() -> None:
    """Clean up all expired sessions from both tables."""
    try:
        if not supabase:
            return
        
        now = _now_iso()
        cleaned = 0
        
        # Clean extra users
        try:
            result = supabase.table(EXTRA_TABLE).delete().lt("expires_at", now).execute()
            if result.data:
                cleaned += len(result.data)
        except Exception as e:
            logger.warning(f"Extra table cleanup error: {e}")
        
        # Clean activity sessions
        try:
            result = supabase.table(ACTIVITY_TABLE).delete().lt("expires_at", now).execute()
            if result.data:
                cleaned += len(result.data)
        except Exception as e:
            logger.warning(f"Activity table cleanup error: {e}")
        
        if cleaned > 0:
            logger.info(f"🧹 Cleaned {cleaned} expired sessions")
            
    except Exception as e:
        logger.error(f"cleanup_expired_sessions error: {e}")

def kick_oldest_public_user() -> Optional[str]:
    """Remove oldest extra user to make room."""
    try:
        if not supabase:
            return None
        
        # Get oldest extra user
        result = supabase.table(EXTRA_TABLE).select("email, login_time") \
            .order("login_time", desc=False) \
            .limit(1) \
            .execute()
        
        if result.data and len(result.data) > 0:
            oldest_email = result.data[0]["email"]
            
            # Delete the oldest user
            supabase.table(EXTRA_TABLE).delete().eq("email", oldest_email).execute()
            
            logger.info(f"👢 Kicked oldest user: {oldest_email}")
            return oldest_email
            
        return None
        
    except Exception as e:
        logger.error(f"kick_oldest_public_user error: {e}")
        return None

# ========== COMPATIBILITY FUNCTIONS ==========
def is_email_allowed(email: str) -> bool:
    """Main function used by backend.py to check access."""
    try:
        email = email.strip().lower()
        
        # Check if VIP
        if is_vip_user(email):
            return True
        
        # Check if has active extra session
        if has_active_extra_session(email):
            return True
        
        # Check if has active VIP session
        if has_active_vip_session(email):
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"is_email_allowed error: {e}")
        return False

def get_user_status(email: str) -> dict:
    """Get comprehensive user status for debugging."""
    email = email.strip().lower()
    return {
        "email": email,
        "is_vip": is_vip_user(email),
        "has_active_extra_session": has_active_extra_session(email),
        "has_active_vip_session": has_active_vip_session(email),
        "is_allowed": is_email_allowed(email),
        "active_extra_users": get_active_extra_users_count(),
        "max_extra_users": MAX_EXTRA_USERS,
        "can_login": can_extra_user_login()
    }

# ========== INITIALIZATION ==========
def initialize_auth():
    """Initialize auth system and clean expired sessions."""
    logger.info("=" * 50)
    logger.info("🔐 AUTH SYSTEM INITIALIZATION")
    logger.info(f"Supabase URL: {SUPABASE_URL}")
    logger.info(f"VIP Users: {get_vip_user_count()}")
    logger.info(f"Active Extra Users: {get_active_extra_users_count()}/{MAX_EXTRA_USERS}")
    logger.info("=" * 50)
    
    # Clean up expired sessions on startup
    cleanup_expired_sessions()

# Auto-initialize when module is imported
if __name__ != "__main__":
    initialize_auth()