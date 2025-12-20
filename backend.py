# ========== GOD MODE TIMETABLE GENERATOR - CLEANED PRODUCTION VERSION ==========
# ALL ISSUES FIXED: No job queue, no duplicates, fixed non-preferred highlighting
from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import re, os, html, time, asyncio, json, logging, itertools, math, sys
from typing import List, Dict, Tuple, Optional, Set, Any
from collections import defaultdict
from dataclasses import dataclass, asdict
from functools import partial, lru_cache
from concurrent.futures import ProcessPoolExecutor
import threading, multiprocessing
from datetime import datetime
from pathlib import Path
from auth_utils import is_email_allowed
import jwt
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
# ========== SETUP ==========
app = FastAPI(title="Timetable Generator API", version="3.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
# Environment-based configuration
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "output.txt")
TIMETABLE_TIMEOUT = int(os.getenv("TIMETABLE_TIMEOUT", "30"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",") if os.getenv("CORS_ORIGINS") else ["*"]
if CORS_ORIGINS != ["*"]:
    CORS_ORIGINS = [origin.strip() for origin in CORS_ORIGINS if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=True,
    max_age=3600,
)

# Rate limiting (simple memory-based)
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_INDEX = {day: i for i, day in enumerate(DAYS_ORDER)}
DAY_ALIASES = {
    "mon": "Monday", "monday": "Monday",
    "tue": "Tuesday", "tuesday": "Tuesday",
    "wed": "Wednesday", "wednesday": "Wednesday",
    "thu": "Thursday", "thursday": "Thursday",
    "fri": "Friday", "friday": "Friday",
    "sat": "Saturday", "saturday": "Saturday"
}

HOUR_SLOTS = [
    ("08:00", "09:00"), ("09:00", "10:00"), ("10:00", "11:00"),
    ("11:00", "12:00"), ("12:00", "13:00"), ("13:00", "14:00"),
    ("14:00", "15:00"), ("15:00", "16:00"), ("16:00", "17:00")
]

# ========== LOGGING ==========
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# Configure logging
log_dir = Path(os.getenv("LOG_DIR", "."))
log_dir.mkdir(exist_ok=True)

try:
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_dir / 'timetable.log',
        maxBytes=10*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
except (PermissionError, OSError, ImportError):
    file_handler = logging.StreamHandler(sys.stdout)

stream_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    handlers=[file_handler, stream_handler],
    force=True
)
logger = logging.getLogger(__name__)

# ========== SIMPLE RATE LIMITING ==========
class RateLimiter:
    """Simple in-memory rate limiter."""
    def __init__(self):
        self.requests: Dict[str, List[float]] = defaultdict(list)
        self.lock = threading.Lock()
    
    def is_allowed(self, client_id: str) -> bool:
        now = time.time()
        with self.lock:
            # Clean old requests
            self.requests[client_id] = [
                req_time for req_time in self.requests[client_id]
                if req_time > now - RATE_LIMIT_WINDOW
            ]
            
            # Check limit
            if len(self.requests[client_id]) >= RATE_LIMIT_REQUESTS:
                return False
            
            self.requests[client_id].append(now)
            return True

rate_limiter = RateLimiter()

def get_client_id(request: Request) -> str:
    """Get client identifier for rate limiting."""
    return request.client.host if request.client else "unknown"

async def check_rate_limit(request: Request):
    """Dependency to check rate limit."""
    client_id = get_client_id(request)
    if not rate_limiter.is_allowed(client_id):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {RATE_LIMIT_WINDOW} seconds."
        )

# ========== PROCESS POOL MANAGEMENT ==========
_process_pool = None
_process_pool_lock = threading.Lock()

def get_process_pool():
    """Get or create process pool (singleton)."""
    global _process_pool
    with _process_pool_lock:
        if _process_pool is None:
            max_workers = max(1, multiprocessing.cpu_count() // 2)
            _process_pool = ProcessPoolExecutor(max_workers=max_workers)
            logger.info(f"Created process pool with {max_workers} workers")
        return _process_pool

# ========== CACHE MANAGEMENT ==========
class CourseCache:
    """Manages course data caching with file monitoring."""
    def __init__(self):
        self._cache = None
        self._cache_mtime = 0
        self._lock = threading.Lock()
    
    def get(self) -> Optional[Dict[str, Any]]:
        """Get cached courses if still valid."""
        with self._lock:
            if not os.path.exists(OUTPUT_FILE):
                return None
            
            current_mtime = os.path.getmtime(OUTPUT_FILE)
            if self._cache is not None and current_mtime == self._cache_mtime:
                return self._cache
            return None
    
    def set(self, courses: Dict[str, Any]):
        """Set cache with current file state."""
        with self._lock:
            if os.path.exists(OUTPUT_FILE):
                self._cache = courses
                self._cache_mtime = os.path.getmtime(OUTPUT_FILE)
    
    def clear(self):
        """Clear cache."""
        with self._lock:
            self._cache = None
            self._cache_mtime = 0

course_cache = CourseCache()

# ========== TIME / PARSING HELPERS ==========
def extract_hours_minutes(t: str) -> Tuple[int, int]:
    """Extract hours and minutes from a time string with validation."""
    t = str(t or "").strip()
    if not t:
        return 0, 0
    
    # Try to parse HH:MM or HH.MM format first
    time_patterns = [
        r'^(\d{1,2})[:\.](\d{2})$',
        r'^(\d{3,4})$',
    ]
    
    for pattern in time_patterns:
        m = re.match(pattern, t)
        if m:
            if len(m.groups()) == 2:
                hours = int(m.group(1))
                minutes = int(m.group(2))
            else:
                digits = m.group(1)
                if len(digits) == 3:
                    hours = int(digits[0])
                    minutes = int(digits[1:3])
                elif len(digits) == 4:
                    hours = int(digits[:2])
                    minutes = int(digits[2:4])
                else:
                    continue
            
            # Validate ranges
            if not (0 <= hours <= 23):
                raise ValueError(f"Hour {hours} must be between 0 and 23")
            if not (0 <= minutes <= 59):
                raise ValueError(f"Minute {minutes} must be between 0 and 59")
            
            return hours, minutes
    
    # Fallback parsing
    digits = re.sub(r'[^0-9]', '', t)
    if not digits:
        return 0, 0
    
    if len(digits) >= 4:
        hours = int(digits[:2])
        minutes = int(digits[2:4])
    elif len(digits) == 3:
        hours = int(digits[0])
        minutes = int(digits[1:3])
    elif len(digits) == 2:
        hours = int(digits)
        minutes = 0
    else:
        hours = int(digits)
        minutes = 0
    
    # Handle minutes >= 60
    if minutes >= 60:
        hours += minutes // 60
        minutes = minutes % 60
    
    # Validate final values
    if not (0 <= hours <= 23):
        hours = max(0, min(23, hours))
    if not (0 <= minutes <= 59):
        minutes = max(0, min(59, minutes))
    
    return hours, minutes

def time_to_minutes(t: str) -> int:
    """Convert time string to minutes since midnight."""
    try:
        h, m = extract_hours_minutes(t)
        return h * 60 + m
    except ValueError:
        logger.warning(f"Invalid time format: {t}")
        return 0

def minutes_to_time(m: int) -> str:
    """Convert minutes since midnight to time string."""
    if m < 0 or m >= 24 * 60:
        raise ValueError(f"Minutes {m} out of range")
    return f"{m // 60:02d}:{m % 60:02d}"

def normalize_time_token(tok: str) -> str:
    """Normalize a time token to HH:MM format."""
    try:
        h, m = extract_hours_minutes(tok)
        return f"{h:02d}:{m:02d}"
    except ValueError:
        logger.warning(f"Invalid time token: {tok}")
        return "00:00"

def parse_single_time_range(part: str) -> Optional[Tuple[str, str]]:
    """Parse a single time range string."""
    part = part.strip()
    if not part:
        return None
    
    # Validate unambiguous ranges
    if '-' in part and part.count('-') > 1:
        logger.warning(f"Ambiguous time range with multiple hyphens: {part}")
        return None
    
    patterns = [
        r'(\d{1,2}(?:[:.]\d{1,2})?)\s*(?:[-–—~=@©]|to)\s*(\d{1,2}(?:[:.]\d{1,2})?)',
        r'(\d{1,2}[:.]\d{2})\s+(\d{1,2}[:.]\d{2})',
        r'(\d{1,2})\s*[-–—~=]\s*(\d{1,2})',
    ]
    
    for p in patterns:
        m = re.search(p, part, flags=re.IGNORECASE)
        if m:
            a, b = m.group(1), m.group(2)
            try:
                a_norm = normalize_time_token(a)
                b_norm = normalize_time_token(b)
                start_min = time_to_minutes(a_norm)
                end_min = time_to_minutes(b_norm)
                
                # Validate logical order
                if start_min >= end_min:
                    logger.warning(f"Invalid time range (start >= end): {a_norm} >= {b_norm}")
                    return None
                
                return a_norm, b_norm
            except ValueError as e:
                logger.warning(f"Invalid time in range: {e}")
                return None
    
    return None

def parse_time_range_string(time_str: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Parse time range string and return ranges with warnings."""
    if not time_str:
        return [], []
    
    ranges: List[Tuple[str, str]] = []
    warnings: List[str] = []
    parts = re.split(r'[,，;、\n]', time_str)
    
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        
        r = parse_single_time_range(part)
        if r:
            ranges.append(r)
        else:
            tokens = re.findall(r'\d{1,2}(?:[:.]\d{1,2})?', part)
            if len(tokens) % 2 == 1:
                warnings.append(f"Odd number of time tokens in '{part}', ignoring last token")
                tokens = tokens[:-1]
            
            for i in range(0, len(tokens), 2):
                try:
                    s = normalize_time_token(tokens[i])
                    e = normalize_time_token(tokens[i + 1])
                    start_min = time_to_minutes(s)
                    end_min = time_to_minutes(e)
                    
                    if start_min < end_min:
                        ranges.append((s, e))
                    else:
                        warnings.append(f"Invalid time range: {s} >= {e}")
                except (ValueError, IndexError) as e:
                    warnings.append(f"Error parsing time tokens: {e}")
    
    return ranges, warnings

# ========== NORMALIZATION FUNCTIONS ==========
def normalize_faculty(name: str) -> str:
    """Normalize faculty name."""
    if not name:
        return ""
    s = str(name).strip()
    s = re.sub(r'[.\s]+$', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s

def normalize_staff_name(name: str) -> str:
    """Normalize staff name (remove titles, lowercase, remove punctuation)."""
    if not name:
        return ""
    name = str(name).strip()
    # Remove titles
    name = re.sub(r'^(Prof\.?|Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Miss)\s+', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', ' ', name)
    name = name.lower()
    # Remove all non-alphanumeric and space characters
    name = re.sub(r'[^\w\s]', '', name)
    name = name.strip()
    return name

def normalize_course_code(code: str) -> str:
    """Normalize course code to uppercase."""
    if not code:
        return ""
    return code.strip().upper()

def normalize_day(day: str) -> Optional[str]:
    """Normalize day name."""
    if not day:
        return None
    day_lower = day.strip().lower()
    for full_day in DAYS_ORDER:
        if full_day.lower() == day_lower:
            return full_day
    for alias, full_day in DAY_ALIASES.items():
        if alias == day_lower:
            return full_day
    if len(day_lower) >= 3:
        return DAY_ALIASES.get(day_lower[:3])
    return None

# ========== PARSER HELPERS ==========
def parse_section_line(line: str) -> Tuple[str, str, str]:
    """Parse section line with case-insensitive handling."""
    line = line.strip()
    if line.lower().startswith("section:"):
        colon_idx = line.lower().find(":")
        line = line[colon_idx + 1:].strip()
    
    patterns = [
        (r'^\s*([^,]+?)\s*,\s*(.+?)\s*-\s*(.+)$', 3),
        (r'^\s*([^,]+?)\s*,\s*(.+)$', 2),
        (r'^\s*(.+)$', 1),
    ]
    
    section_code = ""
    dept = ""
    faculty = ""
    
    for pat, count in patterns:
        m = re.match(pat, line)
        if m:
            section_code = m.group(1).strip()
            if count >= 2:
                dept = m.group(2).strip()
            if count >= 3:
                faculty = normalize_faculty(m.group(3))
            break
    
    return section_code, dept, faculty

# ========== COURSE FINDER HELPER ==========
@lru_cache(maxsize=128)
def find_course_code(subject_code: str, course_codes_str: str) -> Optional[str]:
    """
    Find a course code by code (case-insensitive, cached).
    
    Args:
        subject_code: Code to look for
        course_codes_str: Comma-separated string of available course codes
    """
    subject_code_upper = subject_code.upper()
    course_codes = course_codes_str.split(',')
    
    # Exact match
    if subject_code_upper in course_codes:
        return subject_code_upper
    
    # Case-insensitive match
    for code in course_codes:
        if code.upper() == subject_code_upper:
            return code
    
    return None

def get_course(courses: Dict[str, Any], subject_code: str) -> Optional[Any]:
    """Get course by code with caching."""
    course_codes_str = ','.join(courses.keys())
    found_code = find_course_code(subject_code, course_codes_str)
    return courses.get(found_code) if found_code else None

# ========== DATA STRUCTURES ==========
@dataclass
class TimeSlot:
    day: str
    start_min: int
    end_min: int
    subject_code: str
    section_code: str
    faculty: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_bitmask(self) -> int:
        day_idx = DAY_INDEX.get(self.day)
        if day_idx is None:
            return 0
        
        mask = 0
        window_start = 8 * 60
        window_end = 17 * 60
        start = max(self.start_min, window_start)
        end = min(self.end_min, window_end)
        
        if start >= end:
            return 0
        
        first_slot = (start - window_start) // 60
        last_slot = (end - 1 - window_start) // 60
        
        for slot_idx in range(first_slot, last_slot + 1):
            bit_pos = day_idx * len(HOUR_SLOTS) + slot_idx
            mask |= (1 << bit_pos)
        
        return mask

    def overlaps(self, other: "TimeSlot") -> bool:
        if self.day != other.day:
            return False
        return max(self.start_min, other.start_min) < min(self.end_min, other.end_min)

@dataclass
class CourseSection:
    subject_code: str
    section_code: str
    faculty: str
    dept: str
    time_slots: List[TimeSlot]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_code": self.subject_code,
            "section_code": self.section_code,
            "faculty": self.faculty,
            "dept": self.dept,
            "time_slots": [ts.to_dict() for ts in self.time_slots]
        }

    @property
    def time_bitmask(self) -> int:
        mask = 0
        for slot in self.time_slots:
            mask |= slot.to_bitmask()
        return mask

    def conflicts_with_bitmask(self, other: "CourseSection") -> bool:
        return (self.time_bitmask & other.time_bitmask) != 0

    def conflicts_with(self, other: "CourseSection") -> bool:
        for a in self.time_slots:
            for b in other.time_slots:
                if a.overlaps(b):
                    return True
        return False

    def get_occupied_days(self) -> Set[str]:
        return {s.day for s in self.time_slots}

    def morning_slot_count(self) -> int:
        """Count slots that are in the morning (before 10:00)."""
        return sum(1 for s in self.time_slots if s.start_min < 10 * 60)

    def evening_slot_count(self) -> int:
        """Count slots that overlap with evening window (15:00-17:00)."""
        evening_start = 15 * 60  # 15:00
        evening_end = 17 * 60    # 17:00
        
        count = 0
        for s in self.time_slots:
            # Check if slot overlaps with evening window
            if not (s.end_min <= evening_start or s.start_min >= evening_end):
                count += 1
        return count

    def has_morning_classes(self) -> bool:
        return any(s.start_min < 10 * 60 for s in self.time_slots)

    def has_evening_classes(self) -> bool:
        evening_start = 15 * 60  # 15:00
        evening_end = 17 * 60    # 17:00
        return any(not (s.end_min <= evening_start or s.start_min >= evening_end) 
                for s in self.time_slots)

    def has_saturday_classes(self) -> bool:
        return any(s.day == "Saturday" for s in self.time_slots)

    def get_staff_name(self):
        return self.faculty.strip() if self.faculty else "Unknown"
    
    def get_normalized_staff_name(self):
        return normalize_staff_name(self.faculty)

@dataclass
class Course:
    code: str
    name: str
    credits: str
    sections: List[CourseSection]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "credits": self.credits,
            "sections": [s.to_dict() for s in self.sections]
        }

# ========== CONSTRAINTS DATA STRUCTURES ==========
@dataclass
class ConstraintViolation:
    type: str
    description: str
    priority: int

CONSTRAINT_PRIORITY = {
    'free_day': 1,
    'max_per_day': 2,
    'no_saturday': 3,
    'no_morning': 4,
    'no_evening': 5,
}

@dataclass
class TimetableWithViolations:
    sections: List[CourseSection]
    violations: List[ConstraintViolation]
    
    def has_violations(self) -> bool:
        return len(self.violations) > 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "violations": [asdict(v) for v in self.violations]
        }

# ========== PARSER ==========
def parse_output_txt(text: str) -> Dict[str, Course]:
    """Parse output.txt content into Course objects."""
    if not text:
        return {}
    
    courses: Dict[str, Course] = {}
    current_subject = None
    current_name = ""
    current_credits = ""
    current_sections: List[CourseSection] = []
    lines = text.splitlines()
    i = 0
    parse_warnings: List[str] = []
    
    while i < len(lines):
        line = lines[i].strip()
        
        # Subject detection
        if line.lower().startswith("subject:"):
            if current_subject is not None and current_sections:
                courses[current_subject] = Course(
                    code=current_subject,
                    name=current_name,
                    credits=current_credits,
                    sections=current_sections.copy()
                )
            
            current_sections = []
            
            # Extract after "subject:"
            colon_idx = line.lower().find("subject:")
            after_subject = line[colon_idx + len("subject:"):].strip()
            
            # Try to match course code and credits
            m = re.match(r'^\s*([^\s]+)(?:\s+\[(\d+)\s+Credits\])?', after_subject, re.IGNORECASE)
            if m:
                current_subject = normalize_course_code(m.group(1))
                current_credits = m.group(2) or ""
            else:
                # Fallback
                parts = after_subject.split()
                current_subject = normalize_course_code(parts[0]) if parts else "UNKNOWN"
                current_credits = ""
                if not parts:
                    parse_warnings.append(f"Empty subject at line {i+1}")
            
            current_name = ""
            i += 1
        
        # Course name detection
        elif line.lower().startswith("course name:"):
            colon_idx = line.lower().find("course name:")
            current_name = line[colon_idx + len("course name:"):].strip()
            i += 1
        
        # Section detection
        elif line.lower().startswith("section:"):
            section_code, dept, faculty = parse_section_line(line)
            if not section_code:
                i += 1
                continue
            
            i += 1
            # Skip metadata lines
            while i < len(lines) and any(k.lower() in lines[i].lower() 
                                        for k in ("Date:", "Type:", "Status:")):
                i += 1
            
            time_slots: List[TimeSlot] = []
            while i < len(lines):
                cur = lines[i].strip()
                cur_lower = cur.lower()
                
                # Break if next section or subject
                if not cur or cur_lower.startswith("section:") or cur_lower.startswith("subject:"):
                    break
                
                # Day matching
                day_found = None
                for day in DAYS_ORDER:
                    if cur_lower.startswith(day.lower() + ":"):
                        day_found = day
                        break
                
                if day_found:
                    colon_idx = cur_lower.find(":")
                    times_part = cur[colon_idx + 1:].strip()
                    ranges, warnings = parse_time_range_string(times_part)
                    parse_warnings.extend(warnings)
                    
                    for s, e in ranges:
                        smin = time_to_minutes(s)
                        emin = time_to_minutes(e)
                        if smin < emin:
                            time_slots.append(
                                TimeSlot(
                                    day=day_found,
                                    start_min=smin,
                                    end_min=emin,
                                    subject_code=current_subject or "",
                                    section_code=section_code,
                                    faculty=faculty
                                )
                            )
                        else:
                            parse_warnings.append(f"Invalid time range {s}-{e} for {day_found}")
                
                i += 1
            
            if time_slots:
                current_sections.append(
                    CourseSection(
                        subject_code=current_subject or "",
                        section_code=section_code,
                        faculty=faculty,
                        dept=dept,
                        time_slots=time_slots
                    )
                )
            else:
                parse_warnings.append(f"Section {section_code} has no valid time slots")
        
        else:
            i += 1
    
    # Add the last course
    if current_subject is not None and current_sections:
        courses[current_subject] = Course(
            code=current_subject,
            name=current_name or current_subject,
            credits=current_credits,
            sections=current_sections.copy()
        )
    
    # Log warnings
    for warning in parse_warnings:
        logger.warning(f"Parse warning: {warning}")
    
    # Filter out courses without sections
    for code in list(courses.keys()):
        if not courses[code].sections:
            logger.warning(f"Course {code} has no sections, removing")
            del courses[code]
    
    return courses

# ========== CACHING ==========
def load_courses(force_reload: bool = False) -> Dict[str, Course]:
    """Load courses with caching and file monitoring."""
    # Check cache first
    cached = course_cache.get()
    if cached is not None and not force_reload:
        return cached
    
    if not os.path.exists(OUTPUT_FILE):
        logger.error(f"{OUTPUT_FILE} not found!")
        return {}
    
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        
        raw = parse_output_txt(content)
        normalized: Dict[str, Course] = {}
        
        for code, course in raw.items():
            norm = normalize_course_code(code)
            course.code = norm
            for section in course.sections:
                section.subject_code = norm
            
            course.sections = [s for s in course.sections if s.time_slots]
            if course.sections:
                normalized[norm] = course
            else:
                logger.warning(f"Course {norm} excluded: all sections have no time slots")
        
        # Update cache
        course_cache.set(normalized)
        
        logger.info(f"Loaded {len(normalized)} courses")
        total_sections = sum(len(c.sections) for c in normalized.values())
        logger.info(f"Total sections: {total_sections}")
        
        return normalized
    
    except Exception as e:
        logger.error(f"Error loading courses: {e}", exc_info=True)
        # Don't cache errors
        course_cache.clear()
        return {}

# ========== SCORING ==========
def score_timetable(selection: List[CourseSection],
                morning_weight: float = 1.0,
                evening_weight: float = 1.0,
                staff_preferences: Dict[str, List[str]] = None,
                staff_strictness: str = "strict",
                constraint_violations: List[ConstraintViolation] = None):
    morning_count = sum(sec.morning_slot_count() for sec in selection)
    evening_count = sum(sec.evening_slot_count() for sec in selection)
    
    score = morning_count * morning_weight + evening_count * evening_weight
    
    if staff_preferences:
        for section in selection:
            course_code = section.subject_code
            if course_code in staff_preferences:
                staff_name = section.get_normalized_staff_name()
                if staff_name in staff_preferences[course_code]:
                    position = staff_preferences[course_code].index(staff_name)
                    score += position * 0.001
                else:
                    if staff_strictness == "strict":
                        score += 1000
                    else:
                        score += 10
    
    if constraint_violations:
        sorted_violations = sorted(constraint_violations, key=lambda v: v.priority)
        for i, violation in enumerate(sorted_violations):
            priority_multiplier = 6 - violation.priority
            score += priority_multiplier * 100
    
    return score

# ========== GOD MODE FINDER ==========
class GodModeTimetableFinder:
    def __init__(self, courses: Dict[str, Course], selected_codes: List[str], 
                max_results: int = 10000, timeout: int = TIMETABLE_TIMEOUT):
        self.courses = courses
        self.selected_codes = selected_codes
        self.max_results = min(max_results, 10000)
        self.timeout = timeout
        self.course_list = [courses[c] for c in selected_codes if c in courses]
        self.all_timetables: List[TimetableWithViolations] = []
        
        # Thread safety
        self._lock = threading.Lock()
        
        # Stats and tracking
        self.staff_warnings: List[Dict[str, Any]] = []
        self.staff_deviations: List[Dict[str, Any]] = []
        self.constraint_violations_summary: Dict[str, int] = defaultdict(int)
        self.timetable_violations_map: Dict[int, List[ConstraintViolation]] = {}
        
        self.stats = {
            'total_combinations': 0,
            'combinations_tried': 0,
            'valid_timetables': 0,
            'time_elapsed': 0,
            'progress': 0.0,
            'constraint_strictness': 'strict',
            'total_violations': 0,
            'violations_by_type': {},
            'coverage_percentage': 0.0,
            'search_complete': False,
            'timeout_triggered': False,
            'timeout': timeout,
            'max_results': self.max_results,
            'search_strategy': '',
            'pruned_combinations': 0
        }
    
    def _add_timetable(self, sections: List[CourseSection], violations: List[ConstraintViolation]) -> TimetableWithViolations:
        """Thread-safe method to add a timetable."""
        timetable = TimetableWithViolations(
            sections=sections.copy(),
            violations=violations.copy()
        )
        
        with self._lock:
            idx = len(self.all_timetables)
            self.all_timetables.append(timetable)
            self.timetable_violations_map[idx] = violations.copy()
            
            for violation in violations:
                self.constraint_violations_summary[violation.type] += 1
            
            self.stats['valid_timetables'] += 1
        
        return timetable

    def find_all_timetables(
        self,
        allow_morning_mode='anything',
        allow_evening_mode='anything',
        allow_saturday=True,
        max_per_day=None,
        need_free_day=False,
        free_day_pref=None,
        staff_preferences: Dict[str, List[str]] = None,
        priority_mode: str = 'staff',
        staff_strictness: str = 'strict',
        constraints_strictness: str = 'strict'
    ) -> Tuple[List[TimetableWithViolations], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """Find all valid timetables with given constraints."""
        with self._lock:
            self.all_timetables = []
            self.staff_warnings = []
            self.staff_deviations = []
            self.constraint_violations_summary = defaultdict(int)
            self.timetable_violations_map = {}
            
            self.stats['constraint_strictness'] = constraints_strictness
            self.stats['valid_timetables'] = 0
            self.stats['search_strategy'] = ''
            self.stats['pruned_combinations'] = 0
        
        logger.info(f"🚀 GOD MODE ACTIVATED - Priority Mode: {priority_mode.upper()}")
        logger.info(f"   Staff Strictness: {staff_strictness}")
        logger.info(f"   Constraints Strictness: {constraints_strictness.upper()}")
        
        filtered_course_list = []
        
        # Filter sections based on priority mode
        if priority_mode == 'staff':
            logger.info("   FILTER ORDER: STAFF → TIME CONSTRAINTS")
            for course in self.course_list:
                temp_sections = self._filter_sections_staff_first(
                    course, staff_preferences, staff_strictness,
                    allow_saturday, allow_morning_mode, allow_evening_mode,
                    constraints_strictness
                )
                if not temp_sections:
                    return [], [], [], self.stats
                filtered_course_list.append(temp_sections)
        else:
            logger.info("   FILTER ORDER: TIME CONSTRAINTS → STAFF")
            for course in self.course_list:
                temp_sections = self._filter_sections_constraints_first(
                    course, staff_preferences, staff_strictness,
                    allow_saturday, allow_morning_mode, allow_evening_mode,
                    constraints_strictness
                )
                if not temp_sections:
                    return [], [], [], self.stats
                filtered_course_list.append(temp_sections)
        
        self.course_list = filtered_course_list
        
        # Calculate total combinations
        total_combinations = 1
        for c in self.course_list:
            total_combinations *= len(c.sections)
        
        self.stats['total_combinations'] = total_combinations
        logger.info(f"   Total combinations after filtering: {total_combinations:,}")
        
        # Choose search strategy
        if total_combinations <= 1_000_000:
            self.stats['search_strategy'] = 'bitmask'
            logger.info("   Strategy: BITMASK BRUTE FORCE")
            timetables = self._find_all_bitmask(
                max_per_day, need_free_day, free_day_pref,
                allow_morning_mode, allow_evening_mode, allow_saturday,
                constraints_strictness
            )
        else:
            self.stats['search_strategy'] = 'recursive_pruned'
            logger.info("   Strategy: RECURSIVE DFS WITH PRUNING")
            timetables = self._find_all_recursive(
                max_per_day, need_free_day, free_day_pref,
                allow_morning_mode, allow_evening_mode, allow_saturday,
                constraints_strictness
            )
        
        # Apply strict staff filtering if needed
        if staff_strictness == 'strict' and staff_preferences:
            timetables = self._apply_strict_staff_filtering(timetables, staff_preferences)
        
        # Update final stats
        with self._lock:
            self.stats['total_violations'] = sum(self.constraint_violations_summary.values())
            self.stats['violations_by_type'] = dict(self.constraint_violations_summary)
            self.stats['search_complete'] = not self.stats.get('timeout_triggered', False) and len(self.all_timetables) < self.max_results
            
            # Calculate coverage percentage safely
            if total_combinations > 0 and self.stats['combinations_tried'] > 0:
                coverage = min(100.0, (self.stats['combinations_tried'] / total_combinations) * 100)
                self.stats['coverage_percentage'] = coverage
            else:
                self.stats['coverage_percentage'] = 0.0
        
        # Log completion status
        if self.stats['timeout_triggered']:
            logger.info(f"   Search stopped due to timeout ({self.timeout}s)")
            logger.info(f"   Coverage: {self.stats['coverage_percentage']:.1f}% of search space explored")
        elif len(self.all_timetables) >= self.max_results:
            logger.info(f"   Search stopped at max results limit ({self.max_results})")
            logger.info(f"   Coverage: {self.stats['coverage_percentage']:.1f}% of search space explored")
        else:
            logger.info(f"   Search completed fully")
            logger.info(f"   Coverage: {self.stats['coverage_percentage']:.1f}% of search space explored")
        
        return self.all_timetables, self.staff_warnings, self.staff_deviations, self.stats

    def _filter_sections_staff_first(self, course, staff_preferences, staff_strictness,
                                allow_saturday, allow_morning_mode, allow_evening_mode,
                                constraints_strictness):
        temp_sections = course.sections.copy()
        
        if staff_preferences and course.code in staff_preferences:
            allowed_staff = staff_preferences[course.code]
            
            if staff_strictness == 'strict':
                staff_filtered = [
                    sec for sec in temp_sections 
                    if sec.get_normalized_staff_name() in allowed_staff
                ]
                
                if staff_filtered:
                    temp_sections = staff_filtered
                else:
                    available_staff = set(sec.get_normalized_staff_name() for sec in temp_sections if sec.get_normalized_staff_name())
                    self.staff_warnings.append({
                        'subject': course.code,
                        'subject_name': course.name,
                        'preferred_staff': allowed_staff,
                        'available_staff': list(available_staff),
                        'message': f"Course {course.code}: No sections with preferred staff available (falling back to all)."
                    })
            else:
                preferred_count = sum(1 for sec in temp_sections if sec.get_normalized_staff_name() in allowed_staff)
                leftover_count = len(temp_sections) - preferred_count
                
                if leftover_count > 0:
                    all_staff = set(sec.get_normalized_staff_name() for sec in temp_sections if sec.get_normalized_staff_name())
                    leftover_staff = all_staff - set(allowed_staff)
                    
                    self.staff_deviations.append({
                        'subject': course.code,
                        'subject_name': course.name,
                        'preferred_staff': allowed_staff,
                        'leftover_staff': list(leftover_staff),
                        'preferred_sections_count': preferred_count,
                        'leftover_sections_count': leftover_count,
                        'message': f"Course {course.code}: {len(leftover_staff)} non-preferred staff included."
                    })
        
        if constraints_strictness == 'strict':
            if not allow_saturday:
                before = len(temp_sections)
                temp_sections = [sec for sec in temp_sections if not sec.has_saturday_classes()]
                self.stats['pruned_combinations'] += before - len(temp_sections)
            
            if allow_morning_mode == 'no':
                before = len(temp_sections)
                temp_sections = [sec for sec in temp_sections if not sec.has_morning_classes()]
                self.stats['pruned_combinations'] += before - len(temp_sections)
            
            if allow_evening_mode == 'no':
                before = len(temp_sections)
                temp_sections = [sec for sec in temp_sections if not sec.has_evening_classes()]
                self.stats['pruned_combinations'] += before - len(temp_sections)
        
        if not temp_sections:
            logger.error(f"   Course {course.code}: No sections after staff-first filtering")
        
        return Course(course.code, course.name, course.credits, temp_sections)

    def _filter_sections_constraints_first(self, course, staff_preferences, staff_strictness,
                                        allow_saturday, allow_morning_mode, allow_evening_mode,
                                        constraints_strictness):
        temp_sections = course.sections.copy()
        
        if constraints_strictness == 'strict':
            if not allow_saturday:
                before = len(temp_sections)
                temp_sections = [sec for sec in temp_sections if not sec.has_saturday_classes()]
                self.stats['pruned_combinations'] += before - len(temp_sections)
            
            if allow_morning_mode == 'no':
                before = len(temp_sections)
                temp_sections = [sec for sec in temp_sections if not sec.has_morning_classes()]
                self.stats['pruned_combinations'] += before - len(temp_sections)
            
            if allow_evening_mode == 'no':
                before = len(temp_sections)
                temp_sections = [sec for sec in temp_sections if not sec.has_evening_classes()]
                self.stats['pruned_combinations'] += before - len(temp_sections)
        
        if staff_preferences and course.code in staff_preferences:
            allowed_staff = staff_preferences[course.code]
            
            if staff_strictness == 'strict':
                staff_filtered = [
                    sec for sec in temp_sections 
                    if sec.get_normalized_staff_name() in allowed_staff
                ]
                
                if staff_filtered:
                    temp_sections = staff_filtered
                else:
                    available_staff = set(sec.get_normalized_staff_name() for sec in temp_sections if sec.get_normalized_staff_name())
                    self.staff_warnings.append({
                        'subject': course.code,
                        'subject_name': course.name,
                        'preferred_staff': allowed_staff,
                        'available_staff': list(available_staff),
                        'message': f"Course {course.code}: No time-compatible sections with preferred staff (falling back to all)."
                    })
            else:
                preferred_count = sum(1 for sec in temp_sections if sec.get_normalized_staff_name() in allowed_staff)
                leftover_count = len(temp_sections) - preferred_count
                
                if leftover_count > 0:
                    all_staff = set(sec.get_normalized_staff_name() for sec in temp_sections if sec.get_normalized_staff_name())
                    leftover_staff = all_staff - set(allowed_staff)
                    
                    self.staff_deviations.append({
                        'subject': course.code,
                        'subject_name': course.name,
                        'preferred_staff': allowed_staff,
                        'leftover_staff': list(leftover_staff),
                        'preferred_sections_count': preferred_count,
                        'leftover_sections_count': leftover_count,
                        'message': f"Course {course.code}: {len(leftover_staff)} non-preferred staff in time-compatible sections."
                    })
        
        if not temp_sections:
            logger.error(f"   Course {course.code}: No sections after constraints-first filtering")
        
        return Course(course.code, course.name, course.credits, temp_sections)

    def _apply_strict_staff_filtering(self, timetables, staff_preferences):
        strict_timetables = []
        for timetable in self.all_timetables:
            all_preferred = True
            for section in timetable.sections:
                if section.subject_code in staff_preferences:
                    if section.get_normalized_staff_name() not in staff_preferences[section.subject_code]:
                        all_preferred = False
                        break
            
            if all_preferred:
                strict_timetables.append(timetable)
            else:
                self.staff_warnings.append({
                    'subject': 'Multiple',
                    'message': 'Timetable rejected in strict mode: uses non-preferred staff'
                })
        
        logger.info(f"   After strict staff filtering: {len(strict_timetables)} timetables")
        return strict_timetables

    def _find_all_bitmask(self, max_per_day, need_free_day, free_day_pref,
                        allow_morning_mode, allow_evening_mode, allow_saturday,
                        constraints_strictness):
        start_time = time.time()
        
        total_combinations = self.stats['total_combinations']
        logger.info(f"BITMASK MODE: {total_combinations:,} combinations")

        checked = 0
        self.stats['combinations_tried'] = 0

        original_indices = list(range(len(self.course_list)))
        section_lists = [course.sections for course in self.course_list]
        
        # Sort by number of sections for better pruning
        sorted_indices = sorted(range(len(section_lists)), key=lambda i: len(section_lists[i]))
        sorted_section_lists = [section_lists[i] for i in sorted_indices]
        
        update_interval = min(1000, max(1, total_combinations // 10)) if total_combinations > 0 else 1
        
        for combination in itertools.product(*sorted_section_lists):
            checked += 1
            self.stats['combinations_tried'] = checked
            
            # Check timeout
            if time.time() - start_time > self.timeout:
                logger.warning(f"Bitmask search timeout reached ({self.timeout} seconds)")
                self.stats['timeout_triggered'] = True
                break

            # Restore original order
            original_order = [None] * len(combination)
            for sorted_idx, section in enumerate(combination):
                original_idx = sorted_indices[sorted_idx]
                original_order[original_idx] = section

            # Check for time conflicts using bitmask
            occupied_bitmask = 0
            valid = True
            for sec in original_order:
                if occupied_bitmask & sec.time_bitmask:
                    valid = False
                    break
                occupied_bitmask |= sec.time_bitmask
            
            if not valid:
                continue

            # Check constraints
            is_valid, violations = self._check_constraints(
                original_order,
                max_per_day=max_per_day,
                need_free_day=need_free_day,
                free_day_pref=free_day_pref,
                allow_morning_mode=allow_morning_mode,
                allow_evening_mode=allow_evening_mode,
                allow_saturday=allow_saturday,
                constraints_strictness=constraints_strictness
            )
            
            if is_valid or constraints_strictness == 'flexible':
                self._add_timetable(original_order, violations)
                
                if len(self.all_timetables) >= self.max_results:
                    logger.warning(f"Reached max results limit: {self.max_results}")
                    break

            if checked % update_interval == 0:
                logger.info(f"Bitmask checked {checked:,} combos, found {len(self.all_timetables):,} valid")

        elapsed = time.time() - start_time
        self.stats.update({
            'time_elapsed': elapsed
        })
        return self.all_timetables

    def _find_all_recursive(self, max_per_day, need_free_day, free_day_pref,
                        allow_morning_mode, allow_evening_mode, allow_saturday,
                        constraints_strictness):
        start_time = time.time()
        self.search_start_time = start_time
        
        constraints = {
            'max_per_day': max_per_day,
            'need_free_day': need_free_day,
            'free_day_pref': free_day_pref,
            'allow_morning_mode': allow_morning_mode,
            'allow_evening_mode': allow_evening_mode,
            'allow_saturday': allow_saturday,
            'constraints_strictness': constraints_strictness
        }
        
        self.stats['combinations_tried'] = 0
        
        # Start recursive search
        self._recursive_search(0, [], 0, constraints)
        
        elapsed = time.time() - start_time
        self.stats.update({
            'time_elapsed': elapsed
        })
        return self.all_timetables

    def _recursive_search(self, course_idx: int, current_selection: List[CourseSection], 
                        current_bitmask: int, kwargs: Dict[str, Any]) -> bool:
        """
        Recursive search with early termination.
        Returns True if search should stop.
        """
        # Check timeout
        if time.time() - self.search_start_time > self.timeout:
            self.stats['timeout_triggered'] = True
            return True
            
        # Check if we have enough results
        if len(self.all_timetables) >= self.max_results:
            return True
        
        # Base case: all courses processed
        if course_idx == len(self.course_list):
            is_valid, violations = self._check_constraints(
                current_selection,
                max_per_day=kwargs.get('max_per_day'),
                need_free_day=kwargs.get('need_free_day'),
                free_day_pref=kwargs.get('free_day_pref'),
                allow_morning_mode=kwargs.get('allow_morning_mode'),
                allow_evening_mode=kwargs.get('allow_evening_mode'),
                allow_saturday=kwargs.get('allow_saturday'),
                constraints_strictness=kwargs.get('constraints_strictness', 'strict')
            )
            
            if is_valid or kwargs.get('constraints_strictness', 'strict') == 'flexible':
                self._add_timetable(current_selection, violations)
            
            self.stats['combinations_tried'] += 1
            return False
        
        # Recursive case: try each section of current course
        course = self.course_list[course_idx]
        
        # Sort sections by time slots count for better pruning
        allowed_sections = sorted(course.sections, key=lambda s: len(s.time_slots))
        
        for section in allowed_sections:
            # Check for time conflicts using bitmask (fast)
            if current_bitmask & section.time_bitmask:
                continue
            
            # Try this section
            if self._recursive_search(
                course_idx + 1, 
                current_selection + [section], 
                current_bitmask | section.time_bitmask,
                kwargs
            ):
                return True  # Early termination requested
        
        return False

    def _check_constraints(self, selection: List[CourseSection], 
                        constraints_strictness: str = 'strict',
                        **kwargs) -> Tuple[bool, List[ConstraintViolation]]:
        """Check constraints and return violations."""
        violations: List[ConstraintViolation] = []
        
        # Free day constraint
        need_free_day = kwargs.get('need_free_day')
        free_day_pref = kwargs.get('free_day_pref')
        if need_free_day:
            occupied_days = set()
            for section in selection:
                occupied_days.update(section.get_occupied_days())
            
            if free_day_pref:
                if free_day_pref in occupied_days:
                    violations.append(ConstraintViolation(
                        type='free_day',
                        description=f'Required free day ({free_day_pref}) has classes',
                        priority=CONSTRAINT_PRIORITY['free_day']
                    ))
            else:
                allowed_days = set(DAYS_ORDER)
                if not (allowed_days - occupied_days):
                    violations.append(ConstraintViolation(
                        type='free_day',
                        description='No free day available',
                        priority=CONSTRAINT_PRIORITY['free_day']
                    ))
        
        # Max classes per day constraint
        max_per_day = kwargs.get('max_per_day')
        if max_per_day:
            day_counts = defaultdict(int)
            for section in selection:
                for day in section.get_occupied_days():
                    day_counts[day] += 1
                    if day_counts[day] > max_per_day:
                        violations.append(ConstraintViolation(
                            type='max_per_day',
                            description=f'{day} has {day_counts[day]} classes (max: {max_per_day})',
                            priority=CONSTRAINT_PRIORITY['max_per_day']
                        ))
        
        # Saturday constraint
        allow_saturday = kwargs.get('allow_saturday')
        if not allow_saturday:
            has_saturday = any(any(slot.day == "Saturday" for slot in section.time_slots) 
                            for section in selection)
            if has_saturday:
                violations.append(ConstraintViolation(
                    type='no_saturday',
                    description='Has Saturday classes',
                    priority=CONSTRAINT_PRIORITY['no_saturday']
                ))
        
        # Morning constraint
        allow_morning_mode = kwargs.get('allow_morning_mode')
        if allow_morning_mode == 'no':
            has_morning = any(section.has_morning_classes() for section in selection)
            if has_morning:
                violations.append(ConstraintViolation(
                    type='no_morning',
                    description='Has morning classes',
                    priority=CONSTRAINT_PRIORITY['no_morning']
                ))
        
        # Evening constraint
        allow_evening_mode = kwargs.get('allow_evening_mode')
        if allow_evening_mode == 'no':
            has_evening = any(section.has_evening_classes() for section in selection)
            if has_evening:
                violations.append(ConstraintViolation(
                    type='no_evening',
                    description='Has evening classes',
                    priority=CONSTRAINT_PRIORITY['no_evening']
                ))
        
        violations.sort(key=lambda v: v.priority)
        
        if constraints_strictness == 'strict' and violations:
            return False, violations
        else:
            return True, violations

# ========== WORKER FUNCTION ==========
def run_search_worker(courses_data: Dict[str, Any], selected_codes: List[str], 
                    max_results: int, timeout: int, kwargs: Dict[str, Any]):
    """Worker function for process pool execution."""
    # Reconstruct courses from serialized data
    courses = {}
    for code, course_data in courses_data.items():
        sections = []
        for sec_data in course_data['sections']:
            time_slots = []
            for ts_data in sec_data['time_slots']:
                time_slots.append(TimeSlot(**ts_data))
            
            sections.append(CourseSection(
                subject_code=sec_data['subject_code'],
                section_code=sec_data['section_code'],
                faculty=sec_data['faculty'],
                dept=sec_data['dept'],
                time_slots=time_slots
            ))
        
        courses[code] = Course(
            code=course_data['code'],
            name=course_data['name'],
            credits=course_data['credits'],
            sections=sections
        )
    
    finder = GodModeTimetableFinder(courses, selected_codes, max_results, timeout)
    result = finder.find_all_timetables(**kwargs)
    return result  # Returns (timetables, warnings, deviations, stats)

# ========== ASYNC WRAPPER ==========
async def run_god_search_async(courses_data: Dict[str, Any], selected_codes: List[str],
                            max_results: int, timeout: int, **kwargs):
    """Run search in a separate process."""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            get_process_pool(),
            partial(run_search_worker, courses_data, selected_codes, max_results, timeout, kwargs)
        )
        return result
    except Exception as e:
        logger.error(f"Error in async search: {e}", exc_info=True)
        raise

# ========== HTML RENDERING ==========
def render_constraint_violations_html(violations):
    if not violations:
        return ""

    violations = sorted(violations, key=lambda v: v.priority)

    labels = {
        "free_day": "free day",
        "no_saturday": "Saturday",
        "no_morning": "morning",
        "no_evening": "evening",
        "max_per_day": "maximum classes per day"
    }

    parts = []
    seen = set()

    for v in violations:
        label = labels.get(v.type, v.type.replace('_', ' '))
        if label not in seen:
            parts.append(label)
            seen.add(label)

    if not parts:
        return ""

    if len(parts) == 1:
        msg = f"Your {parts[0]} requirement was violated."
    elif len(parts) == 2:
        msg = f"Your {parts[0]} and {parts[1]} requirements were violated."
    else:
        msg = f"Your {', '.join(parts[:-1])}, and {parts[-1]} requirements were violated."

    return f"""
    <div style="
        padding:12px;
        margin-bottom:15px;
        background:rgba(239,68,68,0.1);
        border:1px solid rgba(239,68,68,0.4);
        border-radius:8px;
        text-align:center;
        font-weight:600;
        color:#fecaca;
    ">
        {msg}
    </div>
    """

class StaffWarningsAggregator:
    """Efficiently aggregates staff warnings across timetables."""
    def __init__(self):
        self.subjects_with_non_preferred = set()
        self.timetable_count_with_non_preferred = 0
        self.total_timetables = 0
    
    def add_timetable(self, timetable, staff_preferences):
        """Add a timetable for analysis."""
        self.total_timetables += 1
        has_non_preferred = False
        
        for section in timetable.sections:
            subject_code = section.subject_code
            if subject_code in staff_preferences:
                section_staff = section.get_normalized_staff_name()
                if section_staff not in staff_preferences[subject_code]:
                    self.subjects_with_non_preferred.add(subject_code)
                    has_non_preferred = True
        
        if has_non_preferred:
            self.timetable_count_with_non_preferred += 1
    
    def get_html(self) -> str:
        """Get HTML representation of warnings."""
        if not self.subjects_with_non_preferred:
            return ""
        
        total_subjects = len(self.subjects_with_non_preferred)
        
        return f"""
        <div style="
            padding:15px;
            margin-bottom:20px;
            background:rgba(245,158,11,0.1);
            border:1px solid rgba(245,158,11,0.4);
            border-radius:8px;
        ">
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
                <div style="
                    width:30px;
                    height:30px;
                    background:#f59e0b;
                    color:white;
                    border-radius:50%;
                    display:flex;
                    align-items:center;
                    justify-content:center;
                    font-weight:bold;
                ">!</div>
                <strong style="color:#f59e0b;">Staff Preference Warning</strong>
            </div>
            <p style="color:#f59e0b; margin:0;">
                {self.timetable_count_with_non_preferred} of {self.total_timetables} timetables use non-preferred staff 
                for {total_subjects} subject(s). In strict mode, these timetables would be excluded.
            </p>
        </div>
        """

def render_single_timetable_html(
    timetable: TimetableWithViolations, 
    idx: int, 
    courses: Dict[str, Course] = None,
    staff_preferences: Dict[str, List[str]] = None, 
    staff_strictness: str = "strict"
) -> str:
    violations = timetable.violations
    sections = timetable.sections
    
    occupancy: Dict[str, List[str]] = {day: [""] * len(HOUR_SLOTS) for day in DAYS_ORDER}
    section_details = []
    
    uses_non_preferred_staff = False
    non_preferred_subjects = set()

    for section in sections:
        course_name = ""
        if courses and section.subject_code in courses:
            course_name = courses[section.subject_code].name
        
        schedule_summary = defaultdict(list)
        for slot in section.time_slots:
            schedule_summary[slot.day].append(
                f"{minutes_to_time(slot.start_min)}-{minutes_to_time(slot.end_min)}"
            )
        
        staff_status = "preferred"
        staff_badge = ""
        # FIXED: Use consistent "non_preferred" (underscore) throughout
        if staff_preferences and section.subject_code in staff_preferences:
            if section.get_normalized_staff_name() not in staff_preferences[section.subject_code]:
                staff_status = "non_preferred"
                uses_non_preferred_staff = True
                non_preferred_subjects.add(section.subject_code)
                staff_badge = '<span style="background:#f59e0b; color:white; padding:2px 6px; border-radius:4px; font-size:0.75rem; margin-left:5px;">Non-Preferred</span>'
                
        
        section_details.append({
            'subject': section.subject_code,
            'course_name': course_name,
            'section': section.section_code,
            'faculty': section.faculty or "N/A",
            'staff_status': staff_status,
            'staff_badge': staff_badge,
            'schedule': dict(schedule_summary)
        })

        for slot in section.time_slots:
            if slot.day not in DAYS_ORDER:
                continue
            for hour_idx, (hs, he) in enumerate(HOUR_SLOTS):
                hs_min = time_to_minutes(hs)
                he_min = time_to_minutes(he)
                if max(slot.start_min, hs_min) < min(slot.end_min, he_min):
                    cell_content = f"{html.escape(section.subject_code)}<br>{html.escape(section.section_code)}"
                    if staff_status == "non_preferred":
                        cell_content += "<br><small style='color:#f59e0b;'>⚠ Non-Preferred</small>"
                    occupancy[slot.day][hour_idx] = cell_content
    
    total_subjects = len(non_preferred_subjects)
    
    html_parts = [
        '<div style="background:#0f172a;border-radius:12px;border:1px solid #1f2937;'
        'margin-bottom:30px;overflow:hidden;">',
        '<div style="background:#020617;padding:15px;border-bottom:1px solid #1f2937;display:flex;justify-content:space-between;align-items:center;">',
        f'<h3 style="color:#e5e7eb;margin:0;font-size:1.2rem;">Timetable #{idx}</h3>',
    ]
    
    badges = []
    
    if violations and len(violations) > 0:
        sorted_violations = sorted(violations, key=lambda v: v.priority)
        highest_priority = sorted_violations[0].priority
        
        if highest_priority <= 2:
            badge_color = "#ef4444"
            badge_bg = "rgba(239,68,68,0.2)"
        elif highest_priority <= 3:
            badge_color = "#f59e0b"
            badge_bg = "rgba(245,158,11,0.2)"
        else:
            badge_color = "#3b82f6"
            badge_bg = "rgba(59,130,246,0.2)"
            
        badges.append(f'''
            <div style="background:{badge_bg};color:{badge_color};padding:6px 12px;border-radius:20px;
                font-size:0.85rem;font-weight:500;border:1px solid {badge_color};">
                ⚠ {len(violations)} Constraint Violation(s)
            </div>
        ''')
    
    if uses_non_preferred_staff and staff_strictness == "flexible":
        badges.append(f'''
            <div style="background:rgba(245,158,11,0.2);color:#f59e0b;padding:6px 12px;border-radius:20px;
                font-size:0.85rem;font-weight:500;border:1px solid rgba(245,158,11,0.4);">
                ⚠ Uses {total_subjects} Non-Preferred Subjects
            </div>
        ''')
    
    if badges:
        html_parts.append('<div style="display:flex; gap:8px;">' + ''.join(badges) + '</div>')
    
    html_parts.append('</div>')
    html_parts.append('<div style="padding:15px;">')
    
    if violations and len(violations) > 0:
        html_parts.append('<div style="margin-bottom:20px;">')
        html_parts.append('<h4 style="color:#e5e7eb;margin-top:0;margin-bottom:10px;font-size:1rem;">Constraint Violations:</h4>')
        html_parts.append(render_constraint_violations_html(violations))
        html_parts.append('</div>')
    
    html_parts.append('<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">')
    html_parts.append('<thead><tr style="background:#020617;">')
    html_parts.append('<th style="border:1px solid #1f2937;padding:10px 5px;color:#9ca3af;'
    'font-weight:500;text-align:left;">Day</th>')

    for start, end in HOUR_SLOTS:
        html_parts.append(
            f'<th style="border:1px solid #1f2937;padding:10px 5px;color:#9ca3af;'
            f'font-weight:500;text-align:center;">{start}-{end}</th>'
        )
    html_parts.append('</tr></thead><tbody>')

    for day in DAYS_ORDER:
        html_parts.append('<tr>')
        html_parts.append(
            f'<td style="border:1px solid #1f2937;padding:10px 5px;font-weight:600;'
            f'color:#e5e7eb;background:#020617;">{day}</td>'
        )
        for hour_idx in range(len(HOUR_SLOTS)):
            cell_content = occupancy[day][hour_idx]
            if cell_content:
                # FIXED: Check for both underscore and hyphen variants
                if "non_preferred" in cell_content or "non-preferred" in cell_content.lower():
                    bg = "rgba(245,158,11,0.3)"
                    fg = "white"
                else:
                    bg = "#1d4ed8"
                    fg = "white"
            else:
                bg = "transparent"
                fg = "#6b7280"
            html_parts.append(
                f'<td style="border:1px solid #1f2937;padding:10px 5px;'
                f'background:{bg};color:{fg};text-align:center;">{cell_content}</td>'
            )
        html_parts.append('</tr>')
    html_parts.append('</tbody></table>')

    html_parts.append(
        '<div style="margin-top:15px;padding:15px;background:#020617;'
        'border-radius:8px;border:1px solid #1f2937;">'
    )
    html_parts.append(
        '<h4 style="color:#e5e7eb;margin-top:0;margin-bottom:10px;font-size:1rem;">'
        'Section Details:</h4>'
    )

    for detail in section_details:
        schedule_html = []
        for day in DAYS_ORDER:
            if day in detail['schedule']:
                times = ", ".join(detail['schedule'][day])
                schedule_html.append(f"{day}: {times}")
        
        faculty_display = detail['faculty']
        if detail['staff_status'] == "non_preferred":
            faculty_display = f'<span style="color:#f59e0b;">{html.escape(detail["faculty"])} ⚠</span>'
        else:
            faculty_display = html.escape(detail["faculty"])
        
        subject_display = f"{html.escape(detail['course_name'])} - {html.escape(detail['subject'])} - {html.escape(detail['section'])}" if detail['course_name'] else f"{html.escape(detail['subject'])} - {html.escape(detail['section'])}"
        
        html_parts.append(
            '<div style="margin-bottom:8px;padding:10px;background:#0f172a;'
            'border-radius:6px;border:1px solid #1f2937;">'
            f'<div style="color:#e5e7eb;font-weight:500;">'
            f'{subject_display} {detail["staff_badge"]}</div>'
            f'<div style="color:#9ca3af;font-size:0.85rem;">Faculty: '
            f'{faculty_display}</div>'
            f'<div style="color:#9ca3af;font-size:0.85rem;">Schedule: '
            f'{" | ".join(schedule_html)}</div>'
            '</div>'
        )

    html_parts.append('</div></div></div>')
    return ''.join(html_parts)

def render_timetable_html_paginated(
    timetables_with_violations: List[TimetableWithViolations],
    courses: Dict[str, Course] = None,
    page: int = 1,
    per_page: int = 10,
    total: int = None,
    staff_preferences: Dict[str, List[str]] = None,
    staff_strictness: str = "strict",
    constraints_strictness: str = "strict",
    stats: Dict[str, Any] = None
) -> str:
    if not timetables_with_violations:
        return '''
        <div style="text-align:center;padding:60px 40px;background:#0f172a;
        border-radius:16px;border:1px solid #1f2937;">
            <h3 style="color:#ef4444;font-size:1.5rem;margin-bottom:15px;">
                ❌ No Valid Timetables Found
            </h3>
            <p style="color:#9ca3af;max-width:600px;margin:0 auto;">
                The search explored possible combinations and found no valid timetables.
                Try relaxing constraints or selecting different courses.
            </p>
        </div>
        '''

    total_timetables = total or len(timetables_with_violations)
    total_pages = (total_timetables + per_page - 1) // per_page if total_timetables > 0 else 1
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, len(timetables_with_violations))
    page_timetables = timetables_with_violations[start_idx:end_idx]

    # Aggregate staff warnings efficiently
    warnings_aggregator = StaffWarningsAggregator()
    for timetable in timetables_with_violations:
        warnings_aggregator.add_timetable(timetable, staff_preferences or {})
    
    html_parts = [
        warnings_aggregator.get_html(),
        
        '<div style="margin-bottom:30px;padding:20px;background:#0f172a;'
        'border-radius:12px;border:1px solid #1f2937;">',
        '<h2 style="color:#e5e7eb;margin:0 0 10px 0;">RESULTS</h2>',
        f'<p style="color:#9ca3af;margin:0 0 10px 0;">Found '
        f'<strong style="color:#10b981">{total_timetables:,}</strong> valid timetables</p>',
        f'<p style="color:#9ca3af;margin:0;">Showing timetables '
        f'<strong>{start_idx + 1}-{end_idx}</strong> of {total_timetables}</p>',
    ]
    
    strictness_info = []
    strictness_info.append(f'<strong>Staff Strictness:</strong> {staff_strictness.capitalize()} mode')
    strictness_info.append(f'<strong>Constraints Strictness:</strong> {constraints_strictness.capitalize()} mode')
    
    html_parts.append(f'''
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:10px;margin-top:10px;">
            <div style="color:#9ca3af;font-size:0.9rem;">{" | ".join(strictness_info)}</div>
        </div>
    ''')
    
    if stats:
        coverage = stats.get('coverage_percentage', 0.0)
        search_complete = stats.get('search_complete', False)
        timeout_triggered = stats.get('timeout_triggered', False)
        max_results = stats.get('max_results', 10000)
        
        if timeout_triggered:
            coverage_text = f"Timeout reached ({stats.get('timeout', 30)}s) - {coverage:.1f}% explored"
        elif coverage >= 99.9 and search_complete:
            coverage_text = "100% of search space explored"
        elif coverage > 0:
            coverage_text = f"{coverage:.1f}% of search space explored"
        else:
            coverage_text = "Coverage not measured (large search space)"
        
        if timeout_triggered:
            guarantee_text = "Search stopped early due to timeout"
        elif len(timetables_with_violations) >= max_results:
            guarantee_text = f"Search stopped at maximum results limit ({max_results})"
        elif coverage >= 99.9 and search_complete:
            guarantee_text = "All likely possibilities explored"
        else:
            guarantee_text = "Substantial search space explored"
        
        html_parts.append(f'''
            <div style="color:#9ca3af;font-size:0.9rem;margin-top:10px;">
                <strong>Search Coverage:</strong> {coverage_text}<br>
                <strong>Guarantee:</strong> {guarantee_text}
            </div>
        ''')
    
    if total_pages > 1:
        html_parts.append(
            '<div style="margin-bottom:20px;padding:15px;background:#020617;'
            'border-radius:8px;border:1px solid #1f2937;">'
            '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">'
        )
        if page > 1:
            html_parts.append(
                f'<button onclick="loadPage({page-1})" '
                'style="background:#3b82f6;color:white;border:none;padding:8px 16px;'
                'border-radius:4px;cursor:pointer;font-weight:500;">← Previous</button>'
            )
        start_page = max(1, page - 4)
        end_page = min(total_pages, start_page + 9)
        for p in range(start_page, end_page + 1):
            if p == page:
                html_parts.append(
                    '<button style="background:#10b981;color:white;border:none;'
                    'padding:8px 12px;border-radius:4px;cursor:pointer;font-weight:bold;" disabled>'
                    f'{p}</button>'
                )
            else:
                html_parts.append(
                    f'<button onclick="loadPage({p})" '
                    'style="background:#374151;color:white;border:none;padding:8px 12px;'
                    'border-radius:4px;cursor:pointer;">'
                    f'{p}</button>'
                )
        if page < total_pages:
            html_parts.append(
                f'<button onclick="loadPage({page+1})" '
                'style="background:#3b82f6;color:white;border:none;padding:8px 16px;'
                'border-radius:4px;cursor:pointer;font-weight:500;">Next →</button>'
            )
        html_parts.append('</div></div>')

    html_parts.append('<div id="timetableResults">')
    for i, timetable in enumerate(page_timetables, start=start_idx + 1):
        html_parts.append(render_single_timetable_html(
            timetable, i, courses, staff_preferences, staff_strictness
        ))
    html_parts.append('</div>')
    
    return '\n'.join(html_parts)

# ========== ROUTES ==========
@app.get("/login")
async def login_page():
    return FileResponse("login.html")

@app.get("/logout")
async def logout():
    """Clear the auth cookie and redirect to login."""
    response = RedirectResponse(url="/login")
    response.delete_cookie("sb-access-token")
    return response

# ... rest of your code ...
@app.middleware("http")
async def auth_guard(request: Request, call_next):
    public_paths = {"/login", "/health", "/logout"}
    
    if request.url.path in public_paths:
        return await call_next(request)
    
    token = request.cookies.get("sb-access-token")
    if not token:
        return RedirectResponse("/login")
    
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        email = payload.get("email")
    except Exception:
        return RedirectResponse("/login")
    
    if not is_email_allowed(email):
        # Show access denied page with logout option
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Access Denied</title>
            <style>
                body {{
                    margin: 0;
                    background: #020617;
                    color: #e5e7eb;
                    font-family: Arial, sans-serif;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                }}
                .card {{
                    background: #0f172a;
                    padding: 40px;
                    width: 400px;
                    border-radius: 12px;
                    border: 1px solid #1f2937;
                    text-align: center;
                }}
                .denied-icon {{
                    font-size: 4rem;
                    margin-bottom: 20px;
                }}
                .logout-btn {{
                    background: #ef4444;
                    color: white;
                    border: none;
                    padding: 12px 24px;
                    border-radius: 6px;
                    font-weight: bold;
                    cursor: pointer;
                    margin-top: 20px;
                    width: 100%;
                }}
                .logout-btn:hover {{
                    background: #dc2626;
                }}
                .info {{
                    color: #9ca3af;
                    margin: 20px 0;
                    padding: 15px;
                    background: rgba(239,68,68,0.1);
                    border-radius: 8px;
                    border: 1px solid rgba(239,68,68,0.3);
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="denied-icon">🚫</div>
                <h2>Access Denied</h2>
                <div class="info">
                    Contact Admin for Access.<br>
                    <strong>Current user:</strong> {email} (not allowed)
                </div>
                <p style="color: #9ca3af; margin-bottom: 20px;">
                    Please log out and try with one of the first 2 accounts.
                </p>
                <button class="logout-btn" onclick="logout()">Logout & Try Different Account</button>
            </div>
            
            <script>
                function logout() {{
                    document.cookie = "sb-access-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
                    window.location.href = "/login";
                }}
            </script>
        </body>
        </html>
        """, status_code=403)
    
    request.state.email = email
    return await call_next(request)

@app.get("/")
async def serve_front(request: Request):
    """Serve the frontend HTML."""
    if os.path.exists("front.html"):
        return FileResponse("front.html")
    return HTMLResponse("<h2>front.html not found. Put front.html in same folder.</h2>")

@app.get("/health")
async def health_check():
    """Health check endpoint for orchestrators."""
    return JSONResponse({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "timetable-generator",
        "version": "3.0.0"
    })

@app.get("/subjects")
async def subjects_list():
    """Get list of all available subjects."""
    courses = load_courses()
    if not courses:
        return JSONResponse({"error": "No courses loaded. Check output.txt file."}, status_code=400)
    
    subjects = []
    for code, course in sorted(courses.items()):
        subjects.append({
            "code": code,
            "name": course.name,
            "credits": course.credits,
            "sections": len(course.sections),
            "has_sections": len(course.sections) > 0,
            "display": f"{code} - {course.name} ({len(course.sections)} sections)"
        })
    
    total_sections = sum(len(c.sections) for c in courses.values())
    subjects.insert(0, {
        "code": "ANYTHING",
        "name": "All subjects",
        "credits": "",
        "sections": total_sections,
        "has_sections": total_sections > 0,
        "display": "ANYTHING - All subjects"
    })
    
    return JSONResponse(subjects)

@app.get("/staff/{subject_code}")
async def get_staff(subject_code: str):
    """Get staff members for a subject."""
    courses = load_courses()
    course = get_course(courses, subject_code)
    
    if not course:
        return JSONResponse({"error": "Subject not found"}, status_code=404)
    
    staff = set()
    for sec in course.sections:
        if sec.faculty:
            staff.add(sec.faculty.strip())
    
    return JSONResponse(sorted(list(staff)))

@app.post("/generate")
async def generate_timetable(
    request: Request,
    selected_subjects: str = Form(""),
    allow_morning: str = Form("anything"),
    allow_evening: str = Form("anything"),
    allow_sat: str = Form("anything"),
    max_classes: str = Form("anything"),
    need_free_day: str = Form("no"),
    free_day: str = Form(""),
    limit: str = Form("1000"),
    page: str = Form("1"),
    preferred_staff: str = Form(""),
    priority_mode: str = Form("staff"),
    staff_strictness: str = Form("strict"),
    constraints_strictness: str = Form("strict")
):
    """Generate timetables based on constraints."""
    # Check rate limit
    await check_rate_limit(request)
    
    # Input size validation
    if len(selected_subjects) > 10000:
        raise HTTPException(status_code=413, detail="Selected subjects input too large")
    
    if len(preferred_staff) > 50000:
        raise HTTPException(status_code=413, detail="Staff preferences input too large")
    
    courses = load_courses()
    if not courses:
        return HTMLResponse(
            '<div style="text-align:center;padding:40px;background:#0f172a;'
            'border-radius:12px;border:1px solid #1f2937;">'
            '<h3 style="color:#ef4444;">❌ No Course Data Found</h3>'
            '<p style="color:#9ca3af;">Please check that output.txt exists and contains valid course data.</p>'
            '</div>'
        )

    # Parse selected subjects
    selected_codes: List[str] = []
    if not selected_subjects or selected_subjects.strip().upper() in ("", "ANYTHING"):
        selected_codes = list(courses.keys())
    else:
        normalized_inputs = [
            normalize_course_code(s.strip())
            for s in selected_subjects.split(",")
            if s.strip()
        ]
        
        for norm_input in normalized_inputs:
            if norm_input in courses:
                selected_codes.append(norm_input)
            else:
                found_course = get_course(courses, norm_input)
                if found_course:
                    selected_codes.append(found_course.code)
        
        if not selected_codes:
            selected_codes = list(courses.keys())
    
    # Parse modes
    def normalize_mode(val: str) -> str:
        v = (val or '').strip().lower()
        if v in ('less', 'no', 'yes', 'anything'):
            return v
        return 'anything'

    morning_mode = normalize_mode(allow_morning)
    evening_mode = normalize_mode(allow_evening)
    sat_mode = normalize_mode(allow_sat)
    allow_saturday_flag = (sat_mode in ('anything', 'yes'))

    # Parse max classes per day
    max_per_day: Optional[int] = None
    if max_classes.lower() != "anything":
        try:
            max_per_day = int(max_classes)
            if max_per_day < 1 or max_per_day > 10:
                max_per_day = None
        except Exception:
            max_per_day = None

    # Parse free day requirements
    require_free = (need_free_day.lower() == "yes")
    free_day_norm = None
    if free_day:
        free_day_norm = normalize_day(free_day)

    # Parse limits
    try:
        max_results = min(int(limit), 10000)
    except Exception:
        max_results = 10000
    
    # Parse priority and strictness
    priority_mode = priority_mode.lower().strip()
    if priority_mode not in ['staff', 'constraints']:
        priority_mode = 'staff'
    
    staff_strictness = staff_strictness.lower().strip()
    if staff_strictness not in ['strict', 'flexible']:
        staff_strictness = 'strict'
    
    constraints_strictness = constraints_strictness.lower().strip()
    if constraints_strictness not in ['strict', 'flexible']:
        constraints_strictness = 'strict'
    
    # Parse staff preferences
    staff_preferences: Dict[str, List[str]] = {}
    if preferred_staff and preferred_staff.strip():
        try:
            preferences_data = json.loads(preferred_staff)
            if not isinstance(preferences_data, list):
                raise ValueError("Preferred staff must be a JSON array")
            
            for item in preferences_data:
                if not isinstance(item, dict) or "subject" not in item or "staff" not in item:
                    raise ValueError("Each preference must have 'subject' and 'staff' keys")
                
                course_code = normalize_course_code(item["subject"])
                if course_code in selected_codes:
                    staff_list = []
                    for s in item["staff"]:
                        if not isinstance(s, str):
                            continue
                        normalized = normalize_staff_name(s)
                        if normalized:
                            staff_list.append(normalized)
                    
                    if staff_list:
                        staff_preferences[course_code] = staff_list
            
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in preferred_staff: {e}")
        except ValueError as e:
            logger.warning(f"Invalid staff preferences format: {e}")
    
    # Convert courses to dict for process pool
    courses_dict = {}
    for code, course in courses.items():
        if code in selected_codes:
            courses_dict[code] = course.to_dict()
    
    # Run search
    try:
        timetables, staff_warnings, staff_deviations, stats = await run_god_search_async(
            courses_dict,
            selected_codes,
            max_results=max_results,
            timeout=TIMETABLE_TIMEOUT,
            allow_morning_mode=morning_mode,
            allow_evening_mode=evening_mode,
            allow_saturday=allow_saturday_flag,
            max_per_day=max_per_day,
            need_free_day=require_free,
            free_day_pref=free_day_norm,
            staff_preferences=staff_preferences,
            priority_mode=priority_mode,
            staff_strictness=staff_strictness,
            constraints_strictness=constraints_strictness
        )
        
        # Sort timetables by score
        timetables.sort(
            key=lambda twv: score_timetable(
                twv.sections,
                morning_weight=1.0 if morning_mode == 'less' else 0.0,
                evening_weight=1.0 if evening_mode == 'less' else 0.0,
                staff_preferences=staff_preferences,
                staff_strictness=staff_strictness,
                constraint_violations=twv.violations
            )
        )
        
    except Exception as e:
        # Log full error but show generic message to user
        logger.error(f"Search failed: {e}", exc_info=True)
        return HTMLResponse(
            f'''
            <div style="text-align:center;padding:40px;background:#0f172a;
            border-radius:12px;border:1px solid #1f2937;">
                <h3 style="color:#ef4444;">❌ Search Error</h3>
                <p style="color:#9ca3af;">
                    An error occurred while searching for timetables.<br>
                    Please try again with different parameters.
                </p>
            </div>
            '''
        )
    
    # Prepare statistics display
    priority_stats = ""
    if priority_mode == 'staff':
        priority_stats = f'''
        <div><strong>Priority Mode:</strong> Staff First</div>
        '''
    else:
        priority_stats = f'''
        <div><strong>Priority Mode:</strong> Constraints First</div>
        '''
    
    strictness_stats = f'''
    <div><strong>Staff Strictness:</strong> {staff_strictness.capitalize()} mode</div>
    <div><strong>Constraints Strictness:</strong> {constraints_strictness.capitalize()} mode</div>
    '''
    
    staff_stats = ""
    if staff_preferences:
        staff_stats = f'''
        <div><strong>Staff preferences:</strong> Applied to {len(staff_preferences)} courses</div>
        <div><strong>Warnings:</strong> {len(staff_warnings)} courses had unavailable preferred staff</div>
        '''
    
    constraint_stats = ""
    if constraints_strictness == 'flexible':
        violations_count = sum(len(twv.violations) for twv in timetables)
        timetables_with_violations = sum(1 for twv in timetables if twv.has_violations())
        constraint_stats = f'''
        <div><strong>Constraint Violations:</strong> {violations_count} total</div>
        <div><strong>Timetables with violations:</strong> {timetables_with_violations} of {len(timetables)}</div>
        '''
    
    coverage = stats.get('coverage_percentage', 0.0)
    search_complete = stats.get('search_complete', False)
    
    if coverage >= 99.9 and search_complete:
        coverage_text = "100% of search space explored"
        guarantee_text = "All likely possibilities explored"
    elif coverage > 0:
        coverage_text = f"{coverage:.1f}% of search space explored"
        guarantee_text = "Substantial search space explored"
    else:
        coverage_text = "Search space explored with pruning"
        guarantee_text = "Substantial search space explored"
    
    stats_html = f'''
    <div style="margin-bottom:20px;padding:20px;background:#0f172a;
    border-radius:12px;border:1px solid #1f2937;">
        <h3 style="color:#e5e7eb;margin-top:0;margin-bottom:10px;">
            SEARCH STATISTICS
        </h3>
        <div style="color:#9ca3af;font-size:0.9rem;
        display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));
        gap:10px;">
            {priority_stats}
            {strictness_stats}
            <div><strong>Search time:</strong> {stats.get('time_elapsed', 0):.2f} seconds</div>
            <div><strong>Total courses:</strong> {len(selected_codes)}</div>
            <div><strong>Valid timetables found:</strong> {len(timetables):,}</div>
            <div><strong>Coverage:</strong> {coverage_text}</div>
            <div><strong>Guarantee:</strong> {guarantee_text}</div>
            {staff_stats}
            {constraint_stats}
        </div>
    </div>
    '''

    try:
        page_num = int(page)
    except Exception:
        page_num = 1

    html_out = render_timetable_html_paginated(
        timetables, 
        courses,
        page=page_num, 
        per_page=10,
        staff_preferences=staff_preferences,
        staff_strictness=staff_strictness,
        constraints_strictness=constraints_strictness,
        stats=stats
    )


# 🔐 email extracted earlier by middleware
    email = request.state.email

    from supabase_client import supabase
    try:
        supabase.table("user_activity").insert({
            "email": email,
            "selected_subjects": selected_subjects,
            "constraints": {
                "morning": allow_morning,
                "evening": allow_evening,
                "saturday": allow_sat,
                "max_classes": max_classes,
                "free_day": free_day
            },
            "staff_preferences": staff_preferences,
            "results_count": len(timetables),
            "coverage": stats.get("coverage_percentage"),
            "search_time": stats.get("time_elapsed")
        }).execute()
    except Exception as e:
        print("SUPABASE INSERT ERROR:", e)

    return HTMLResponse(stats_html + html_out)


@app.get("/reload_courses")
async def reload_courses_endpoint():
    """Force reload courses from file."""
    course_cache.clear()
    load_courses(force_reload=True)
    return JSONResponse({"status": "Courses reloaded"})

# ========== CLEANUP ==========
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    global _process_pool
    if _process_pool:
        _process_pool.shutdown(wait=True)
        _process_pool = None
        logger.info("Process pool shutdown")

# ========== MAIN ==========
if __name__ == "__main__":
    import uvicorn
    
    # Log startup information
    logger.info("=" * 80)
    logger.info("🚀 TIMETABLE GENERATOR - CLEAN PRODUCTION VERSION 3.0")
    logger.info(f"✅ Job queue removed, simple rate limiting kept")
    logger.info(f"✅ Fixed non-preferred highlighting bug")
    logger.info(f"✅ Rate limiting: {RATE_LIMIT_REQUESTS} req/{RATE_LIMIT_WINDOW}s")
    logger.info(f"✅ CORS Origins: {CORS_ORIGINS}")
    logger.info("=" * 80)
    
    # Load courses at startup
    logger.info("Loading courses...")
    courses = load_courses()
    logger.info(f"Loaded {len(courses)} courses at startup")
    
    # Determine port
    port = int(os.getenv("PORT", "8000"))
    
    # Run server with single worker (recommended with ProcessPoolExecutor)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        timeout_keep_alive=int(os.getenv("TIMEOUT_KEEP_ALIVE", "30")),
        workers=int(os.getenv("UVICORN_WORKERS", "1"))
    )