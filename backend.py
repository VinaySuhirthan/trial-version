# ========== GOD MODE TIMETABLE GENERATOR - FIXED VERSION ==========
# ALL ISSUES FIXED: Case-sensitivity, Progress consistency
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import re, os, html, time, copy, asyncio, json, uuid, logging, itertools, math
from typing import List, Dict, Tuple, Optional, Set, Any
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import partial
from concurrent.futures import ThreadPoolExecutor
import threading, multiprocessing
from datetime import datetime

# ========== SETUP ==========
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_FILE = "output.txt"
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
import sys, io

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

file_handler = logging.FileHandler('timetable.log', encoding='utf-8')
stream_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)
logging.basicConfig(level=logging.INFO, handlers=[file_handler, stream_handler])
logger = logging.getLogger(__name__)

# ========== TIME / PARSING HELPERS ==========
def extract_hours_minutes(t: str) -> Tuple[int, int]:
    t = str(t or "").strip()
    if not t:
        return 0, 0
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
    if minutes >= 60:
        hours += minutes // 60
        minutes = minutes % 60
    return hours, minutes

def time_to_minutes(t: str) -> int:
    h, m = extract_hours_minutes(t)
    return h * 60 + m

def minutes_to_time(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"

def normalize_time_token(tok: str) -> str:
    h, m = extract_hours_minutes(tok)
    return f"{h:02d}:{m:02d}"

def parse_single_time_range(part: str) -> Optional[Tuple[str, str]]:
    part = part.strip()
    if not part:
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
            a_norm = normalize_time_token(a)
            b_norm = normalize_time_token(b)
            if time_to_minutes(a_norm) >= time_to_minutes(b_norm):
                logger.warning(f"Invalid time range: {a_norm} >= {b_norm}")
                return None
            return a_norm, b_norm
    return None

def parse_time_range_string(time_str: str) -> List[Tuple[str, str]]:
    if not time_str:
        return []
    ranges: List[Tuple[str, str]] = []
    parts = re.split(r'[,，;、\n]', time_str)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        r = parse_single_time_range(part)
        if r:
            ranges.append(r)
        else:
            tokens = re.findall(r'\d{1,2}(?:[:.]\d{1,2})?', part)
            for i in range(0, len(tokens) - 1, 2):
                s = normalize_time_token(tokens[i])
                e = normalize_time_token(tokens[i + 1])
                if time_to_minutes(s) < time_to_minutes(e):
                    ranges.append((s, e))
                else:
                    logger.warning(f"Invalid time range in token parsing: {s} >= {e}")
    return ranges

# ========== NORMALIZATION ==========
def normalize_faculty(name: str) -> str:
    if not name:
        return ""
    s = str(name).strip()
    s = re.sub(r'[.\s]+$', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s

def normalize_staff_name(name: str) -> str:
    if not name:
        return ""
    name = str(name).strip()
    name = re.sub(r'^(Prof\.?|Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Miss)\s+', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', ' ', name)
    name = name.lower()
    name = re.sub(r'[^\w\s\.]', '', name)
    name = name.strip()
    return name

def normalize_course_code(code: str) -> str:
    return code.strip().upper()

def normalize_day(day: str) -> Optional[str]:
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
    line = line.strip()
    # FIXED: Case-insensitive section detection
    if line.lower().startswith("section:"):
        line = line[len("Section:"):].strip() if line.startswith("Section:") else line[len("section:"):].strip()
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

# ========== DATA STRUCTURES ==========
@dataclass
class TimeSlot:
    day: str
    start_min: int
    end_min: int
    subject_code: str
    section_code: str
    faculty: str = ""

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
        return sum(1 for s in self.time_slots if s.start_min < 10 * 60)

    def evening_slot_count(self) -> int:
        return sum(1 for s in self.time_slots if s.start_min < 17 * 60 and s.end_min > 15 * 60)

    def has_morning_classes(self) -> bool:
        return any(s.start_min < 10 * 60 for s in self.time_slots)

    def has_evening_classes(self) -> bool:
        return any(s.start_min < 17 * 60 and s.end_min > 15 * 60 for s in self.time_slots)

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

# ========== PARSER ==========
def parse_output_txt(text: str) -> Dict[str, Course]:
    if not text:
        return {}
    courses: Dict[str, Course] = {}
    current_subject = None
    current_name = ""
    current_credits = ""
    current_sections: List[CourseSection] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # FIXED: Case-insensitive subject detection
        if line.lower().startswith("subject:"):
            if current_subject is not None:
                courses[current_subject] = Course(
                    code=current_subject,
                    name=current_name,
                    credits=current_credits,
                    sections=current_sections.copy()
                )
            current_sections = []
            
            # FIXED: Case-insensitive matching for the entire line
            line_lower = line.lower()
            if "subject:" in line_lower:
                # Extract after "subject:" (case-insensitive)
                after_subject = line[line_lower.find("subject:") + len("subject:"):].strip()
                # Try to match course code and credits
                m = re.match(r'^\s*([^\s]+)(?:\s+\[(\d+)\s+Credits\])?', after_subject, re.IGNORECASE)
                if m:
                    current_subject = normalize_course_code(m.group(1))
                    current_credits = m.group(2) or ""
                else:
                    # Fallback: take first word as course code
                    parts = after_subject.split()
                    if parts:
                        current_subject = normalize_course_code(parts[0])
                        current_credits = ""
            else:
                # Fallback parsing
                parts = line.split(":", 1)
                if len(parts) > 1:
                    code_part = parts[1].strip().split()[0] if parts[1].strip() else "UNKNOWN"
                    current_subject = normalize_course_code(code_part)
                else:
                    current_subject = "UNKNOWN"
                current_credits = ""
            
            current_name = ""
            i += 1
        # FIXED: Case-insensitive course name detection
        elif line.lower().startswith("course name:"):
            if ":" in line:
                current_name = line.split(":", 1)[1].strip()
            i += 1
        # FIXED: Case-insensitive section detection
        elif line.lower().startswith("section:"):
            section_code, dept, faculty = parse_section_line(line)
            if not section_code:
                i += 1
                continue
            i += 1
            # Skip metadata lines (case-insensitive)
            while i < len(lines) and any(k.lower() in lines[i].lower() for k in ("Date:", "Type:", "Status:")):
                i += 1
            time_slots: List[TimeSlot] = []
            while i < len(lines):
                cur = lines[i].strip()
                # FIXED: Case-insensitive detection for next section or subject
                cur_lower = cur.lower()
                if not cur or cur_lower.startswith("section:") or cur_lower.startswith("subject:"):
                    break
                
                # FIXED: Case-insensitive day matching
                day_found = None
                for day in DAYS_ORDER:
                    if cur_lower.startswith(day.lower() + ":"):
                        day_found = day
                        break
                
                if day_found:
                    times_part = cur[len(day_found) + 1:].strip()
                    ranges = parse_time_range_string(times_part)
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
                logger.warning(f"Section {section_code} has no time slots")
        else:
            i += 1
    if current_subject is not None:
        courses[current_subject] = Course(
            code=current_subject,
            name=current_name or current_subject,
            credits=current_credits,
            sections=current_sections.copy()
        )
    for code, course in list(courses.items()):
        if not course.sections:
            logger.warning(f"Course {code} has no sections")
    return courses

def load_courses() -> Dict[str, Course]:
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
        logger.info(f"Loaded {len(normalized)} courses")
        total_sections = sum(len(c.sections) for c in normalized.values())
        logger.info(f"Total sections: {total_sections}")
        return normalized
    except Exception as e:
        logger.error(f"Error loading courses: {e}", exc_info=True)
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

# ========== GOD MODE FINDER (FIXED VERSION) ==========
class GodModeTimetableFinder:
    def __init__(self, courses: Dict[str, Course], selected_codes: List[str], max_results: int = 10000, timeout: int = 30):
        self.courses = courses
        self.selected_codes = selected_codes
        self.max_results = min(max_results, 10000)
        self.timeout = timeout
        self.course_list = [courses[c] for c in selected_codes if c in courses]
        self.all_timetables: List[TimetableWithViolations] = []
        self.search_start_time = None
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
            'max_results': self.max_results
        }
    
    def _add_timetable(self, sections: List[CourseSection], violations: List[ConstraintViolation]) -> TimetableWithViolations:
        timetable = TimetableWithViolations(
            sections=sections.copy(),
            violations=violations.copy()
        )
        self.all_timetables.append(timetable)
        
        idx = len(self.all_timetables) - 1
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
    ):
        self.all_timetables = []
        self.staff_warnings = []
        self.staff_deviations = []
        self.constraint_violations_summary = defaultdict(int)
        self.timetable_violations_map = {}
        
        self.stats['constraint_strictness'] = constraints_strictness
        self.stats['valid_timetables'] = 0
        
        logger.info(f"🚀 GOD MODE ACTIVATED - Priority Mode: {priority_mode.upper()}")
        logger.info(f"   Staff Strictness: {staff_strictness}")
        logger.info(f"   Constraints Strictness: {constraints_strictness.upper()}")
        
        filtered_course_list = []
        
        if priority_mode == 'staff':
            logger.info("   FILTER ORDER: STAFF → TIME CONSTRAINTS")
            
            for course in self.course_list:
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
                            logger.info(f"   Course {course.code}: {len(staff_filtered)} sections after strict staff filter")
                        else:
                            available_staff = set(sec.get_normalized_staff_name() for sec in temp_sections if sec.get_normalized_staff_name())
                            self.staff_warnings.append({
                                'subject': course.code,
                                'subject_name': course.name,
                                'preferred_staff': allowed_staff,
                                'available_staff': list(available_staff),
                                'message': f"Course {course.code}: No sections with preferred staff available (falling back to all)."
                            })
                            logger.warning(f"   Course {course.code}: No preferred staff, using all {len(temp_sections)} sections")
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
                        logger.info(f"   Course {course.code}: {len(temp_sections)} sections (flexible mode)")
                
                if constraints_strictness == 'strict':
                    if not allow_saturday:
                        before = len(temp_sections)
                        temp_sections = [sec for sec in temp_sections if not sec.has_saturday_classes()]
                        if before != len(temp_sections):
                            logger.info(f"   Course {course.code}: Saturday filter removed {before - len(temp_sections)} sections")
                    
                    if allow_morning_mode == 'no':
                        before = len(temp_sections)
                        temp_sections = [sec for sec in temp_sections if not sec.has_morning_classes()]
                        if before != len(temp_sections):
                            logger.info(f"   Course {course.code}: Morning filter removed {before - len(temp_sections)} sections")
                    
                    if allow_evening_mode == 'no':
                        before = len(temp_sections)
                        temp_sections = [sec for sec in temp_sections if not sec.has_evening_classes()]
                        if before != len(temp_sections):
                            logger.info(f"   Course {course.code}: Evening filter removed {before - len(temp_sections)} sections")
                
                if not temp_sections:
                    logger.error(f"   Course {course.code}: No sections after staff-first filtering")
                    return [], [], []
                
                filtered_course = Course(
                    code=course.code,
                    name=course.name,
                    credits=course.credits,
                    sections=temp_sections
                )
                filtered_course_list.append(filtered_course)
        
        else:
            logger.info("   FILTER ORDER: TIME CONSTRAINTS → STAFF")
            
            for course in self.course_list:
                temp_sections = course.sections.copy()
                
                if constraints_strictness == 'strict':
                    if not allow_saturday:
                        before = len(temp_sections)
                        temp_sections = [sec for sec in temp_sections if not sec.has_saturday_classes()]
                        if before != len(temp_sections):
                            logger.info(f"   Course {course.code}: Saturday filter removed {before - len(temp_sections)} sections")
                    
                    if allow_morning_mode == 'no':
                        before = len(temp_sections)
                        temp_sections = [sec for sec in temp_sections if not sec.has_morning_classes()]
                        if before != len(temp_sections):
                            logger.info(f"   Course {course.code}: Morning filter removed {before - len(temp_sections)} sections")
                    
                    if allow_evening_mode == 'no':
                        before = len(temp_sections)
                        temp_sections = [sec for sec in temp_sections if not sec.has_evening_classes()]
                        if before != len(temp_sections):
                            logger.info(f"   Course {course.code}: Evening filter removed {before - len(temp_sections)} sections")
                
                if staff_preferences and course.code in staff_preferences:
                    allowed_staff = staff_preferences[course.code]
                    
                    if staff_strictness == 'strict':
                        staff_filtered = [
                            sec for sec in temp_sections 
                            if sec.get_normalized_staff_name() in allowed_staff
                        ]
                        
                        if staff_filtered:
                            temp_sections = staff_filtered
                            logger.info(f"   Course {course.code}: {len(staff_filtered)} sections after strict staff filter")
                        else:
                            available_staff = set(sec.get_normalized_staff_name() for sec in temp_sections if sec.get_normalized_staff_name())
                            self.staff_warnings.append({
                                'subject': course.code,
                                'subject_name': course.name,
                                'preferred_staff': allowed_staff,
                                'available_staff': list(available_staff),
                                'message': f"Course {course.code}: No time-compatible sections with preferred staff (falling back to all)."
                            })
                            logger.warning(f"   Course {course.code}: No preferred staff in time-filtered sections, using {len(temp_sections)} sections")
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
                        logger.info(f"   Course {course.code}: {len(temp_sections)} time-filtered sections (flexible mode)")
                
                if not temp_sections:
                    logger.error(f"   Course {course.code}: No sections after constraints-first filtering")
                    return [], [], []
                
                filtered_course = Course(
                    code=course.code,
                    name=course.name,
                    credits=course.credits,
                    sections=temp_sections
                )
                filtered_course_list.append(filtered_course)
        
        self.course_list = filtered_course_list
        
        total_combinations = math.prod([len(c.sections) for c in self.course_list]) if self.course_list else 0
        self.stats['total_combinations'] = total_combinations
        logger.info(f"   Total combinations after filtering: {total_combinations:,}")
        
        if total_combinations <= 1_000_000:
            logger.info("   Strategy: BITMASK BRUTE FORCE")
            timetables = self.find_all_bitmask(
                max_per_day, need_free_day, free_day_pref,
                allow_morning_mode, allow_evening_mode, allow_saturday,
                constraints_strictness
            )
        else:
            logger.info("   Strategy: RECURSIVE DFS")
            timetables = self.find_all_recursive(
                max_per_day, need_free_day, free_day_pref,
                allow_morning_mode, allow_evening_mode, allow_saturday,
                constraints_strictness
            )
        
        if staff_strictness == 'strict' and staff_preferences:
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
            
            self.all_timetables = strict_timetables
            logger.info(f"   After strict staff filtering: {len(self.all_timetables)} timetables")
        
        self.stats['total_violations'] = sum(self.constraint_violations_summary.values())
        self.stats['violations_by_type'] = dict(self.constraint_violations_summary)
        self.stats['search_complete'] = not self.stats.get('timeout_triggered', False) and len(self.all_timetables) < self.max_results
        
        if total_combinations > 0 and self.stats['combinations_tried'] > 0:
            self.stats['coverage_percentage'] = min(100.0, (self.stats['combinations_tried'] / total_combinations) * 100)
        
        if self.stats['timeout_triggered']:
            logger.info(f"   Search stopped due to timeout ({self.timeout}s)")
            logger.info(f"   Coverage: {self.stats['coverage_percentage']:.1f}% of search space explored")
        elif len(self.all_timetables) >= self.max_results:
            logger.info(f"   Search stopped at max results limit ({self.max_results})")
            logger.info(f"   Coverage: {self.stats['coverage_percentage']:.1f}% of search space explored")
        else:
            logger.info(f"   Search completed fully")
            logger.info(f"   Coverage: 100% of search space explored")
        
        return self.all_timetables, self.staff_warnings, self.staff_deviations

    def find_all_bitmask(self, max_per_day, need_free_day, free_day_pref,
                        allow_morning_mode, allow_evening_mode, allow_saturday,
                        constraints_strictness):
        start_time = time.time()
        
        total_combinations = math.prod([len(c.sections) for c in self.course_list]) if self.course_list else 0
        self.stats['total_combinations'] = total_combinations
        logger.info(f"BITMASK MODE: {total_combinations:,} combinations")

        checked = 0
        self.stats['combinations_tried'] = 0

        original_indices = list(range(len(self.course_list)))
        section_lists = [course.sections for course in self.course_list]
        
        sorted_indices = sorted(range(len(section_lists)), key=lambda i: len(section_lists[i]))
        sorted_section_lists = [section_lists[i] for i in sorted_indices]
        
        update_interval = min(1000, max(1, total_combinations // 10)) if total_combinations > 0 else 1
        
        for combination in itertools.product(*sorted_section_lists):
            checked += 1
            self.stats['combinations_tried'] = checked
            
            if time.time() - start_time > self.timeout:
                logger.warning(f"Bitmask search timeout reached ({self.timeout} seconds)")
                self.stats['timeout_triggered'] = True
                break

            original_order = [None] * len(combination)
            for sorted_idx, section in enumerate(combination):
                original_idx = sorted_indices[sorted_idx]
                original_order[original_idx] = section

            occupied_bitmask = 0
            valid = True
            for sec in original_order:
                if occupied_bitmask & sec.time_bitmask:
                    valid = False
                    break
                occupied_bitmask |= sec.time_bitmask
            
            if not valid:
                continue

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

    def find_all_recursive(self, max_per_day, need_free_day, free_day_pref,
                          allow_morning_mode, allow_evening_mode, allow_saturday,
                          constraints_strictness):
        start_time = time.time()
        self.search_start_time = start_time
        total_combinations = math.prod([len(c.sections) for c in self.course_list]) if self.course_list else 0
        self.stats['total_combinations'] = total_combinations
        
        logger.info(f"RECURSIVE MODE: {total_combinations:,} combos")
        
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
        
        self._recursive_search(0, [], constraints)
        
        elapsed = time.time() - start_time
        self.stats.update({
            'time_elapsed': elapsed
        })
        return self.all_timetables

    def _recursive_search(self, course_idx: int, current_selection: List[CourseSection], kwargs: Dict[str, Any]):
        if time.time() - self.search_start_time > self.timeout:
            self.stats['timeout_triggered'] = True
            return
            
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
                
                if len(self.all_timetables) >= self.max_results:
                    return
            return
        
        course = self.course_list[course_idx]
        
        allowed_sections = sorted(course.sections, key=lambda s: len(s.time_slots))
        
        for section in allowed_sections:
            self.stats['combinations_tried'] += 1
            
            conflict = False
            for selected in current_selection:
                if selected.conflicts_with(section):
                    conflict = True
                    break
            
            if not conflict:
                self._recursive_search(course_idx + 1, current_selection + [section], kwargs)

    def _check_constraints(self, selection: List[CourseSection], 
                          constraints_strictness: str = 'strict',
                          **kwargs) -> Tuple[bool, List[ConstraintViolation]]:
        violations: List[ConstraintViolation] = []
        
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
        
        allow_morning_mode = kwargs.get('allow_morning_mode')
        if allow_morning_mode == 'no':
            has_morning = any(section.has_morning_classes() for section in selection)
            if has_morning:
                violations.append(ConstraintViolation(
                    type='no_morning',
                    description='Has morning classes',
                    priority=CONSTRAINT_PRIORITY['no_morning']
                ))
        
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

# ========== ASYNC WRAPPER ==========
executor = ThreadPoolExecutor(max_workers=4)

async def run_god_search_async(finder, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, partial(finder.find_all_timetables, **kwargs))

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
        if v.type in labels and v.type not in seen:
            parts.append(labels[v.type])
            seen.add(v.type)

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

def render_staff_warnings_html(
    timetables: List[TimetableWithViolations], 
    staff_preferences: Dict[str, List[str]] = None
) -> str:
    if not staff_preferences or not timetables:
        return ""
    
    subjects_with_non_preferred = set()
    timetable_count_with_non_preferred = 0
    
    for timetable in timetables:
        has_non_preferred = False
        for section in timetable.sections:
            subject_code = section.subject_code
            if subject_code in staff_preferences:
                section_staff = section.get_normalized_staff_name()
                preferred_staff_list = staff_preferences[subject_code]
                
                if section_staff not in preferred_staff_list:
                    subjects_with_non_preferred.add(subject_code)
                    has_non_preferred = True
        
        if has_non_preferred:
            timetable_count_with_non_preferred += 1
    
    total_subjects = len(subjects_with_non_preferred)
    
    if total_subjects == 0:
        return ""
    
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
            {timetable_count_with_non_preferred} of {len(timetables)} timetables use non-preferred staff 
            for {total_subjects} subject(s). In strict mode, these timetables would be excluded.
        </p>
    </div>
    """

def render_single_timetable_html(
    timetable: TimetableWithViolations, 
    idx: int, 
    courses: Dict[str, Course] = None,
    staff_preferences: Dict[str, List[str]] = None, 
    staff_strictness: str = "strict",
    constraint_violations: List[ConstraintViolation] = None
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
        if staff_preferences and section.subject_code in staff_preferences:
            if section.get_normalized_staff_name() not in staff_preferences[section.subject_code]:
                staff_status = "non-preferred"
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
                    if staff_status == "non-preferred":
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
                if "Non-Preferred" in cell_content:
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
        if detail['staff_status'] == "non-preferred":
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
    total_pages = (total_timetables + per_page - 1) // per_page
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, len(timetables_with_violations))
    page_timetables = timetables_with_violations[start_idx:end_idx]

    html_parts = [
        render_staff_warnings_html(timetables_with_violations, staff_preferences),
        
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
            coverage_text = f"Timeout reached ({stats['timeout']}s) - {coverage:.1f}% explored"
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
@app.get("/")
def serve_front():
    if os.path.exists("front.html"):
        return FileResponse("front.html")
    return HTMLResponse("<h2>front.html not found. Put front.html in same folder.</h2>")

@app.get("/subjects")
async def subjects_list():
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
    courses = load_courses()
    # FIXED: Case-insensitive course code lookup
    subject_code_upper = subject_code.upper()
    
    # Try exact match first
    if subject_code_upper in courses:
        subject = courses[subject_code_upper]
    else:
        # Try case-insensitive match
        matching_codes = [code for code in courses.keys() if code.upper() == subject_code_upper]
        if matching_codes:
            subject = courses[matching_codes[0]]
        else:
            return JSONResponse({"error": "Subject not found"}, status_code=404)
    
    staff = set()
    for sec in subject.sections:
        if sec.faculty:
            staff.add(sec.faculty.strip())
    
    return JSONResponse(sorted(list(staff)))

@app.post("/generate")
async def generate_timetable(
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
    courses = load_courses()
    if not courses:
        return HTMLResponse(
            '<div style="text-align:center;padding:40px;background:#0f172a;'
            'border-radius:12px;border:1px solid #1f2937;">'
            '<h3 style="color:#ef4444;">❌ No Course Data Found</h3>'
            '<p style="color:#9ca3af;">Please check that output.txt exists and contains valid course data.</p>'
            '</div>'
        )

    if not selected_subjects or selected_subjects.strip().upper() in ("", "ANYTHING"):
        selected_codes = list(courses.keys())
        logger.info(f"Selected ALL courses: {len(selected_codes)} courses")
    else:
        selected_codes: List[str] = []
        normalized_inputs = [
            normalize_course_code(s.strip())
            for s in selected_subjects.split(",")
            if s.strip()
        ]
        for norm_input in normalized_inputs:
            if norm_input in courses:
                selected_codes.append(norm_input)
            else:
                matched = False
                for course_code in courses.keys():
                    if norm_input in course_code or course_code in norm_input:
                        selected_codes.append(course_code)
                        matched = True
                        break
                if not matched:
                    logger.warning(f"Could not match subject code: {norm_input}")
        if not selected_codes:
            selected_codes = list(courses.keys())
            logger.info("No matches found, selecting all courses")
        logger.info(f"Selected {len(selected_codes)} courses")

    def normalize_mode(val: str) -> str:
        v = (val or '').strip().lower()
        if v in ('less', 'no', 'yes', 'anything'):
            return v
        if v in ('true', 'allow'):
            return 'anything'
        return 'anything'

    morning_mode = normalize_mode(allow_morning)
    evening_mode = normalize_mode(allow_evening)
    sat_mode = normalize_mode(allow_sat)
    allow_saturday_flag = (sat_mode in ('anything', 'yes'))

    max_per_day: Optional[int] = None
    if max_classes.lower() != "anything":
        try:
            max_per_day = int(max_classes)
        except Exception:
            max_per_day = None

    require_free = (need_free_day.lower() == "yes")
    free_day_norm = None
    if free_day:
        free_day_norm = normalize_day(free_day)
        if not free_day_norm:
            logger.warning(f"Invalid free day input: {free_day}")

    try:
        max_results = min(int(limit), 10000)
    except Exception:
        max_results = 10000
    
    priority_mode = priority_mode.lower().strip()
    if priority_mode not in ['staff', 'constraints']:
        priority_mode = 'staff'
    
    staff_strictness = staff_strictness.lower().strip()
    if staff_strictness not in ['strict', 'flexible']:
        staff_strictness = 'strict'
    
    constraints_strictness = constraints_strictness.lower().strip()
    if constraints_strictness not in ['strict', 'flexible']:
        constraints_strictness = 'strict'
    
    staff_preferences: Dict[str, List[str]] = {}
    if preferred_staff and preferred_staff.strip():
        try:
            preferences_data = json.loads(preferred_staff)
            for item in preferences_data:
                if "subject" in item and "staff" in item:
                    course_code = normalize_course_code(item["subject"])
                    if course_code in selected_codes:
                        staff_list = [normalize_staff_name(s) for s in item["staff"] if s.strip()]
                        if staff_list:
                            staff_preferences[course_code] = staff_list
                            logger.info(f"Staff preference for {course_code}: {len(staff_list)} staff members")
        except json.JSONDecodeError:
            for rule in preferred_staff.split("|"):
                if ":" in rule:
                    course_code, staff_names = rule.split(":", 1)
                    course_code_norm = normalize_course_code(course_code.strip())
                    if course_code_norm in selected_codes:
                        staff_list = [normalize_staff_name(s) for s in staff_names.split(",") if s.strip()]
                        if staff_list:
                            staff_preferences[course_code_norm] = staff_list
                            logger.info(f"Staff preference for {course_code_norm}: {len(staff_list)} staff members")
    
    logger.info(f"🚀 ADVANCED SEARCH MODE ACTIVATED")
    logger.info(f"   Priority Mode: {priority_mode}")
    logger.info(f"   Staff Strictness: {staff_strictness}")
    logger.info(f"   Constraints Strictness: {constraints_strictness}")
    logger.info(f"   Courses: {len(selected_codes)}")
    if staff_preferences:
        for course, staff in staff_preferences.items():
            logger.info(f"     {course}: {len(staff)} preferred staff")

    start_time = time.time()
    filtered_courses = {code: courses[code] for code in selected_codes if code in courses}

    finder = GodModeTimetableFinder(filtered_courses, selected_codes, max_results=max_results, timeout=30)
    
    timetables, staff_warnings, staff_deviations = await run_god_search_async(
        finder,
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
    
    stats = finder.stats

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

    end_time = time.time()
    search_time = end_time - start_time

    total_combinations = stats.get('total_combinations', 0)
    if total_combinations == 0 and hasattr(finder, 'course_list'):
        try:
            total_combinations = math.prod([len(c.sections) for c in finder.course_list])
        except Exception:
            total_combinations = 0

    priority_stats = ""
    if priority_mode == 'staff':
        priority_stats = f'''
        <div><strong>Priority Mode:</strong> Staff First (prefers your staff, falls back to available if needed)</div>
        '''
    else:
        priority_stats = f'''
        <div><strong>Priority Mode:</strong> Constraints First (strict constraints, then staff)</div>
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
        <div><strong>Flexible deviations:</strong> {len(staff_deviations)} courses using flexible staff selection</div>
        '''
    
    constraint_stats = ""
    if constraints_strictness == 'flexible' and stats.get('total_violations', 0) > 0:
        constraint_stats = f'''
        <div><strong>Constraint Violations:</strong> {stats.get('total_violations', 0)} total violations</div>
        <div><strong>Timetables with violations:</strong> {sum(1 for twv in timetables if twv.has_violations())} of {len(timetables)}</div>
        '''
    
    coverage = stats.get('coverage_percentage', 0.0)
    timeout_triggered = stats.get('timeout_triggered', False)
    max_results_val = stats.get('max_results', 10000)
    
    if timeout_triggered:
        coverage_text = f"Timeout reached ({stats['timeout']}s) - {coverage:.1f}% explored"
        guarantee_text = "Search stopped early due to timeout"
    elif coverage >= 99.9 and stats.get('search_complete', False):
        coverage_text = "100% of search space explored"
        guarantee_text = "All likely possibilities explored"
    elif coverage > 0:
        coverage_text = f"{coverage:.1f}% of search space explored"
        guarantee_text = "Substantial search space explored"
    else:
        coverage_text = "Coverage not measured (large search space)"
        guarantee_text = "Search space explored with pruning"
    
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
            <div><strong>Search time:</strong> {search_time:.2f} seconds</div>
            <div><strong>Total possible combinations:</strong> {total_combinations:,}</div>
            <div><strong>Combinations tried:</strong> {stats.get('combinations_tried', 0):,}</div>
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
        filtered_courses,
        page=page_num, 
        per_page=10,
        staff_preferences=staff_preferences,
        staff_strictness=staff_strictness,
        constraints_strictness=constraints_strictness,
        stats=stats
    )
    return HTMLResponse(stats_html + html_out)

# ========== DEBUG ENDPOINTS ==========
@app.get("/debug_staff/{course_code}")
async def debug_staff(course_code: str):
    courses = load_courses()
    # FIXED: Case-insensitive course code lookup
    course_code_upper = course_code.upper()
    
    if course_code_upper not in courses:
        # Try case-insensitive match
        matching_codes = [code for code in courses.keys() if code.upper() == course_code_upper]
        if not matching_codes:
            return JSONResponse({"error": "Course not found"}, status_code=404)
        course_code_upper = matching_codes[0]
    
    course = courses[course_code_upper]
    staff_details = []
    
    for i, section in enumerate(course.sections):
        staff_details.append({
            "section": section.section_code,
            "raw_faculty": section.faculty,
            "normalized": section.get_normalized_staff_name(),
            "has_time_slots": len(section.time_slots) > 0,
            "time_slots": [(slot.day, minutes_to_time(slot.start_min), minutes_to_time(slot.end_min)) 
                          for slot in section.time_slots]
        })
    
    unique_staff = sorted(set([s["normalized"] for s in staff_details if s["normalized"]]))
    
    return JSONResponse({
        "course": course_code_upper,
        "total_sections": len(course.sections),
        "unique_staff": unique_staff,
        "sections": staff_details
    })

@app.get("/test_staff_filter/{course_code}")
async def test_staff_filter(course_code: str, staff_name: str = ""):
    courses = load_courses()
    # FIXED: Case-insensitive course code lookup
    course_code_upper = course_code.upper()
    
    if course_code_upper not in courses:
        # Try case-insensitive match
        matching_codes = [code for code in courses.keys() if code.upper() == course_code_upper]
        if not matching_codes:
            return JSONResponse({"error": "Course not found"}, status_code=404)
        course_code_upper = matching_codes[0]
    
    course = courses[course_code_upper]
    
    test_preference = normalize_staff_name(staff_name) if staff_name else "test_prof"
    
    all_sections = course.sections
    
    filtered_sections = [
        sec for sec in all_sections 
        if sec.get_normalized_staff_name() == test_preference
    ]
    
    return JSONResponse({
        "course": course_code_upper,
        "test_staff_name": staff_name,
        "normalized_test_name": test_preference,
        "total_sections": len(all_sections),
        "filtered_sections": len(filtered_sections),
        "all_staff_names": sorted(set([sec.get_normalized_staff_name() for sec in all_sections])),
        "matching_sections": [
            {
                "section": sec.section_code,
                "staff": sec.faculty,
                "normalized_staff": sec.get_normalized_staff_name()
            }
            for sec in filtered_sections
        ]
    })

# ========== CLEANUP ==========
@app.on_event("shutdown")
async def shutdown_event():
    executor.shutdown(wait=False)
    logger.info("Executors shutdown")

# ========== MAIN ==========
if __name__ == "__main__":
    import uvicorn
    logger.info("=" * 80)
    logger.info("🚀 TIMETABLE GENERATOR - COMPLETELY FIXED VERSION")
    logger.info("✅ ALL CRITICAL BUGS FIXED")
    logger.info("✅ ALL MAJOR ISSUES RESOLVED")
    logger.info("✅ ALL MINOR ISSUES ADDRESSED")
    logger.info("=" * 80)
    logger.info("Starting server on http://0.0.0.0:8000")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        timeout_keep_alive=30
    )