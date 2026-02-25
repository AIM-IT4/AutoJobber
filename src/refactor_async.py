import re
from pathlib import Path

def patch_file(p: Path):
    text = p.read_text('utf-8')
    
    # 1. Change sync_playwright to async_playwright
    text = text.replace("sync_playwright", "async_playwright")
    
    # 2. Defs to async def. Careful around __init__ and staticmethods/internal pure functions
    lines = text.split('\n')
    for i in range(len(lines)):
        m = re.match(r'^(\s*)def (\w+)\(', lines[i])
        if m:
            indent = m.group(1)
            name = m.group(2)
            if name not in ['__init__', '_result', '_persist_results', '_sanitize_filename', 
                            '_radio_group_label', '_question_terms', '_container_locators', 
                            '_as_bool', '_keyword_matches', '_normalize']:
                lines[i] = f"{indent}async def {name}("
                
    text = '\n'.join(lines)
    
    # 3. Add await to specific playwright locs
    methods_to_await = [
        r'\bclick\(', r'\bfill\(', r'\bcount\(\)', r'\bis_visible\(\)', 
        r'\bevaluate\(', r'\binput_value\(\)', r'\bget_attribute\(', 
        r'\bpress\(', r'\bcheck\(\)', r'\bselect_option\(', 
        r'\bis_checked\(\)', r'\bset_input_files\(', 
        r'\bwait_for_timeout\(', r'\bgoto\(', r'\bwait_for_selector\(', 
        r'\bwait_for_load_state\(', r'\binner_text\(', r'\bscreenshot\(',
        r'\bclose\(\)', r'\bnew_page\(\)', r'\blaunch\(', 
        r'\blaunch_persistent_context\('
    ]
    
    for method in methods_to_await:
        # Avoid awaiting things that are already awaited, just in case
        # also avoid things like os.path.join etc (though those methods aren't in those)
        text = re.sub(rf'(?<!await )([a-zA-Z0-9_\.\[\]]+)\.{method}', rf'await \1.{method}', text)
        
    text = text.replace("await self.debug_path", "self.debug_path")
    text = text.replace("await os.getenv", "os.getenv")
    text = text.replace("with async_playwright()", "async with async_playwright()")
    text = text.replace("with source_page.expect_popup(", "async with source_page.expect_popup(")
    # Some evaluate logic
    text = text.replace("await self._click_first", "await self._click_first")
    text = text.replace("await self._click_first_with_retry", "await self._click_first_with_retry")
    text = text.replace("await self._accept_cookies", "await self._accept_cookies")
    text = text.replace("await self._has_auth_error", "await self._has_auth_error")
    text = text.replace("await self._click_option", "await self._click_option")
    text = text.replace("await self._value_applied", "await self._value_applied")
    text = text.replace("await self._apply_question_driven_answers", "await self._apply_question_driven_answers")
    text = text.replace("await self._apply_state_fallback", "await self._apply_state_fallback")
    text = text.replace("await self._upload_resume_if_needed", "await self._upload_resume_if_needed")
    text = text.replace("await self._field_label", "await self._field_label")
    text = text.replace("await self._start_workday_flow", "await self._start_workday_flow")
    text = text.replace("await self._prefer_sign_in_path", "await self._prefer_sign_in_path")
    text = text.replace("await self._fill_signin_credentials", "await self._fill_signin_credentials")
    text = text.replace("await self._run_application_steps", "await self._run_application_steps")
    text = text.replace("await filler.fill_visible_fields()", "await filler.fill_visible_fields()")
    text = text.replace("await self._choose_application_page", "await self._choose_application_page")
    text = text.replace("await self._follow_redirect_notice", "await self._follow_redirect_notice")

    # fix double awaits
    text = text.replace("await await", "await")

    p.write_text(text, encoding='utf-8')

import sys
patch_file(Path('job_apply_agent/apply/engine.py'))
patch_file(Path('job_apply_agent/apply/form_filler.py'))
print("Refactored to async.")
