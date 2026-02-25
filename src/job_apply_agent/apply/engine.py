from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import BrowserContext, Error, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from job_apply_agent.apply.form_filler import FormFiller
from job_apply_agent.models import ApplicationResult, JobPosting, RankedJob

APPLY_SELECTORS = [
    "button:has-text('Apply')",
    "a:has-text('Apply')",
    "button:has-text('Apply for job')",
    "a:has-text('Apply for job')",
    "button:has-text('Apply Now')",
    "a:has-text('Apply Now')",
    "[data-selector-name='job-apply-link']",
    "a.job-apply",
    "a.ajd_btn__apply",
    "a[data-apply-url]",
    "a[href*='myworkdayjobs.com'][href*='/apply']",
    "[data-automation-id*='apply']",
]

APPLY_START_SELECTORS = [
    "a:has-text('Apply Manually')",
    "button:has-text('Apply Manually')",
    "a:has-text('Autofill with Resume')",
    "button:has-text('Autofill with Resume')",
]

SIGN_IN_SELECTORS = [
    "[data-automation-id='click_filter'][aria-label='Sign In']",
    "[role='button'][aria-label='Sign In']",
    "button:has-text('Sign In')",
    "a:has-text('Sign In')",
]

CONTINUE_SELECTORS = [
    "button:has-text('Save and Continue')",
    "[role='button'][aria-label='Save and Continue']",
    "[data-automation-id='click_filter'][aria-label='Save and Continue']",
    "button:has-text('Continue')",
    "[role='button'][aria-label='Continue']",
    "[data-automation-id='click_filter'][aria-label='Continue']",
    "button:has-text('Next')",
    "[role='button'][aria-label='Next']",
    "[data-automation-id='click_filter'][aria-label='Next']",
    "button:has-text('Review and Submit')",
    "[role='button'][aria-label='Review and Submit']",
    "[data-automation-id='click_filter'][aria-label='Review and Submit']",
    "button:has-text('Review')",
    "[role='button'][aria-label='Review']",
    "[data-automation-id='click_filter'][aria-label='Review']",
    "a:has-text('Save and Continue')",
    "a:has-text('Continue')",
    "[data-automation-id*='continue']",
    "[data-automation-id*='next']",
    "[data-automation-id*='saveAndContinue']",
]

COOKIE_SELECTORS = [
    "button:has-text('Accept Cookies')",
    "button:has-text('Accept')",
]

SUBMIT_SELECTORS = [
    "button:has-text('Submit')",
    "[role='button'][aria-label='Submit']",
    "[data-automation-id='click_filter'][aria-label='Submit']",
    "button:has-text('Send Application')",
    "[role='button'][aria-label='Send Application']",
    "[data-automation-id='click_filter'][aria-label='Send Application']",
    "button:has-text('Complete Application')",
    "[role='button'][aria-label='Complete Application']",
    "[data-automation-id='click_filter'][aria-label='Complete Application']",
    "button:has-text('Submit Application')",
    "[role='button'][aria-label='Submit Application']",
    "[data-automation-id='click_filter'][aria-label='Submit Application']",
    "input[type='submit']",
    "[data-automation-id*='submit']",
]

logger = logging.getLogger(__name__)


class ApplicationEngine:
    def __init__(
        self,
        profile: dict,
        output_dir: Path,
        headless: bool = True,
        auto_submit: bool = False,
        session_dir: Path | None = None,
        max_role_seconds: int = 210,
    ) -> None:
        self.profile = profile
        self.headless = headless
        self.auto_submit = auto_submit
        self.output_dir = output_dir
        self.session_dir = session_dir
        self.max_role_seconds = max_role_seconds
        contact = profile.get("contact", {}) or {}
        self.account_password = str(profile.get("account_password", "")).strip()
        self.login_email = str(profile.get("workday_login_email", contact.get("email", ""))).strip()
        self.login_password = str(
            profile.get("workday_login_password", self.account_password)
        ).strip()
        self._session_authenticated = False
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.session_dir is not None:
            self.session_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize_filename(raw: str) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in raw)
        return safe[:90] or "job"

    @staticmethod
    async def _click_first(page: Page, selectors: list[str], timeout_ms: int = 1800) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                total = min(await locator.count(), 6)
            except Exception:
                continue

            for idx in range(total):
                try:
                    candidate = locator.nth(idx)
                    if not await candidate.is_visible():
                        continue
                    try:
                        await candidate.click(timeout=timeout_ms)
                        return True
                    except Exception:
                        await candidate.click(timeout=timeout_ms, force=True)
                        return True
                except Exception:
                    continue
        return False

    async def _click_first_with_retry(
        self,
        page: Page,
        selectors: list[str],
        total_timeout_ms: int = 9000,
        click_timeout_ms: int = 2200,
        interval_ms: int = 450,
    ) -> bool:
        deadline = time.time() + (total_timeout_ms / 1000.0)
        while time.time() < deadline:
            if await self._click_first(page, selectors, timeout_ms=click_timeout_ms):
                return True
            try:
                await page.wait_for_timeout(interval_ms)
            except Exception:
                break
        return False

    @staticmethod
    async def _click_page_footer_next(page: Page) -> bool:
        selectors = [
            "button[data-automation-id='pageFooterNextButton']",
            "[data-automation-id='click_filter'][aria-label='Save and Continue']",
            "button:has-text('Save and Continue')",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                total = min(await loc.count(), 6)
            except Exception:
                continue
            for idx in range(total):
                try:
                    btn = loc.nth(idx)
                    if not await btn.is_visible():
                        continue

                    aria_disabled = (await btn.get_attribute("aria-disabled") or "").strip().lower()
                    disabled_attr = await btn.get_attribute("disabled")
                    if aria_disabled in {"true", "1"} or disabled_attr is not None:
                        continue

                    try:
                        await btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass

                    try:
                        await btn.click(timeout=4000)
                        return True
                    except Exception:
                        await btn.click(timeout=4000, force=True)
                        return True
                except Exception:
                    continue
        return False

    @staticmethod
    async def _current_apply_step(page: Page) -> str:
        candidates = [
            "[data-automation-id='progressBarActiveStep'] label:last-child",
            "[data-automation-id='progressBarActiveStep'] label",
            "h2",
            "h1",
        ]
        for selector in candidates:
            try:
                label = page.locator(selector).first
                if await label.count() == 0 or not await label.is_visible():
                    continue
                text = (await label.inner_text(timeout=1200)).strip()
                if text:
                    return text
            except Exception:
                continue
        return ""

    async def _accept_cookies(self, page: Page) -> bool:
        return await self._click_first(page, COOKIE_SELECTORS, timeout_ms=4000)

    async def _has_auth_error(self, page: Page) -> bool:
        try:
            text = (await page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            return False
        markers = [
            "wrong email address",
            "wrong email",
            "wrong password",
            "account might be locked",
            "account is locked",
            "invalid credentials",
        ]
        return any(marker in text for marker in markers)

    async def _has_validation_errors(self, page: Page) -> bool:
        try:
            text = (await page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            return False
        markers = [
            "errors found",
            "this field is required",
            "this value is required",
            "required and must have a value",
            "please correct",
        ]
        return any(marker in text for marker in markers)

    async def _has_runtime_page_error(self, page: Page) -> bool:
        try:
            text = (await page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            return False
        markers = [
            "something went wrong",
            "please refresh the page",
            "try again",
            "we're sorry",
        ]
        return any(marker in text for marker in markers)

    async def _requires_human_verification(self, page: Page) -> bool:
        selectors = [
            "iframe[src*='hcaptcha.com']",
            "[data-hcaptcha-widget-id]",
            "textarea[name='h-captcha-response']",
            "textarea[name='g-recaptcha-response']",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                if await loc.count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _is_oracle_email_verification_step(self, page: Page) -> bool:
        try:
            url = page.url.lower()
        except Exception:
            url = ""
        if "/apply/email" not in url:
            return False
        try:
            text = (await page.locator("body").inner_text(timeout=2200)).lower()
        except Exception:
            return False
        return "enter your email address" in text or "authentication screen" in text

    async def _accept_oracle_legal_disclaimer(self, page: Page) -> int:
        updated = 0
        selectors = [
            "#legal-disclaimer-checkbox",
            "label[for='legal-disclaimer-checkbox']",
            ".apply-flow-input-checkbox__button",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if await loc.count() == 0:
                    continue
                tag = (await loc.evaluate("(el) => el.tagName.toLowerCase()")).strip()
                if tag == "input":
                    checked = False
                    try:
                        checked = await loc.is_checked()
                    except Exception:
                        checked = False
                    if not checked:
                        await loc.check(force=True)
                        updated += 1
                        break
                    continue
                await loc.click(timeout=2000, force=True)
                updated += 1
                break
            except Exception:
                continue
        return updated

    async def _is_already_applied_page(self, page: Page) -> bool:
        try:
            text = (await page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            return False
        markers = [
            "you've already applied for this job",
            "you have already applied for this job",
            "already applied for this job",
            "view my applications",
        ]
        return any(marker in text for marker in markers)

    async def _is_logged_in(self, page: Page) -> bool:
        signed_out_selectors = [
            "[data-automation-id='utilityButtonSignIn']",
            "a:has-text('Sign In')",
            "button:has-text('Sign In')",
        ]
        for selector in signed_out_selectors:
            try:
                loc = page.locator(selector)
                total = min(await loc.count(), 4)
            except Exception:
                continue
            for idx in range(total):
                try:
                    node = loc.nth(idx)
                    if not await node.is_visible():
                        continue
                    text = (await node.inner_text(timeout=800)).strip().lower()
                    if text == "sign in" or text.endswith(" sign in"):
                        return False
                except Exception:
                    continue

        selectors = [
            "#accountSettingsButton",
            "[data-automation-id='utilityButtonAccountTasksMenu']",
            "button:has-text('Candidate Home')",
            "a:has-text('Candidate Home')",
            "[data-automation-id='navigationItem-Candidate Home']",
            "[data-automation-id='utilityButtonSettings']",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible():
                    return True
            except Exception:
                continue

        if self.login_email:
            try:
                header_text = (await page.locator("header").inner_text(timeout=1200)).lower()
                if self.login_email.lower() in header_text:
                    return True
            except Exception:
                pass
        return False

    async def _signin_containers(self, page: Page):
        selectors = [
            "[role='dialog']",
            "[aria-modal='true']",
            "form",
            "div[class*='modal']",
            "section",
        ]
        containers = []
        for selector in selectors:
            try:
                nodes = page.locator(selector)
                total = min(await nodes.count(), 8)
            except Exception:
                continue

            for idx in range(total):
                try:
                    candidate = nodes.nth(idx)
                    if not await candidate.is_visible():
                        continue

                    password_count = await candidate.locator("input[type='password']").count()
                    if password_count != 1:
                        continue

                    email_like_count = await candidate.locator(
                        "input[type='email'], input[autocomplete='username'], input[type='text']"
                    ).count()
                    if email_like_count == 0:
                        continue

                    sign_in_controls = candidate.locator(
                        "button:has-text('Sign In'), [role='button'][aria-label='Sign In'], a:has-text('Sign In')"
                    )
                    if await sign_in_controls.count() == 0:
                        continue

                    containers.append(candidate)
                except Exception:
                    continue
        return containers

    async def _is_signin_prompt_visible(self, page: Page) -> bool:
        containers = await self._signin_containers(page)
        if containers:
            return True

        try:
            sign_in_heading = page.locator("h1:has-text('Sign In'), h2:has-text('Sign In')").first
            if await sign_in_heading.count() == 0 or not await sign_in_heading.is_visible():
                return False
        except Exception:
            return False

        try:
            password_visible = await page.locator("input[type='password']").first.is_visible()
            email_visible = await page.locator(
                "input[type='email'], input[autocomplete='username'], input[type='text']"
            ).first.is_visible()
            return password_visible and email_visible
        except Exception:
            return False

    async def _fill_signin_credentials(self, page: Page) -> int:
        if not self.login_email or not self.login_password:
            return 0

        containers = await self._signin_containers(page)
        if not containers:
            return 0

        for container in containers:
            filled = 0
            try:
                email_fields = container.locator(
                    "input[type='email'], input[autocomplete='username'], input[type='text'], input:not([type])"
                )
                for idx in range(min(await email_fields.count(), 4)):
                    field = email_fields.nth(idx)
                    if not await field.is_visible():
                        continue
                    current = await field.input_value()
                    if not current.strip():
                        await field.fill(self.login_email)
                    filled += 1
                    break
            except Exception:
                pass

            try:
                password_field = container.locator("input[type='password']").first
                if await password_field.count() > 0 and await password_field.is_visible():
                    current_pw = await password_field.input_value()
                    if not current_pw.strip():
                        await password_field.fill(self.login_password)
                    filled += 1
            except Exception:
                pass

            if filled > 0:
                return filled

        return 0

    async def _click_modal_signin(self, page: Page) -> bool:
        containers = await self._signin_containers(page)
        for scope in containers:
            try:
                button = scope.locator(
                    "button:has-text('Sign In'), [role='button'][aria-label='Sign In'], a:has-text('Sign In')"
                ).first
                if await button.count() > 0 and await button.is_visible():
                    await button.click(timeout=5000)
                    return True
            except Exception:
                continue
        return False

    async def _close_signin_prompt(self, page: Page) -> bool:
        closed = await self._click_first(
            page,
            [
                "[role='dialog'] button[aria-label='Close']",
                "[aria-modal='true'] button[aria-label='Close']",
                "button[aria-label='Close']",
                "[role='dialog'] button:has-text('Close')",
            ],
            timeout_ms=1200,
        )
        if closed:
            return True
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(150)
            return not await self._is_signin_prompt_visible(page)
        except Exception:
            return False

    @staticmethod
    async def _is_create_account_form_visible(page: Page) -> bool:
        try:
            create_btn = page.locator("button:has-text('Create Account')").first
            if await create_btn.count() > 0 and await create_btn.is_visible():
                return True
        except Exception:
            pass
        return False

    @staticmethod
    async def _close_mini_modal(page: Page) -> bool:
        selectors = [
            "button[aria-label='Close']",
            "button:has(svg)",
            "button:has-text('×')",
        ]
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=1500)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _is_create_account_step(page: Page) -> bool:
        try:
            heading = page.locator("h2:has-text('Create Account')").first
            if await heading.count() > 0 and await heading.is_visible():
                return True
        except Exception:
            pass
        try:
            body = (await page.locator("body").inner_text(timeout=1500)).lower()
        except Exception:
            return False
        return "create account" in body and "already have an account" in body

    async def _attempt_create_account_fallback(self, page: Page, filler: FormFiller) -> tuple[bool, int, bool]:
        opened = await self._click_first_with_retry(
            page,
            [
                "button[data-automation-id='createAccountLink']",
                "a[data-automation-id='createAccountLink']",
                "button:has-text('Create Account')",
                "a:has-text('Create Account')",
            ],
            total_timeout_ms=4500,
            click_timeout_ms=1800,
            interval_ms=350,
        )
        if opened:
            try:
                await page.wait_for_timeout(450)
            except Exception:
                pass

        filled = 0
        if self.login_email:
            for selector in [
                "input[data-automation-id='email']",
                "input[type='email']",
                "input[autocomplete='email']",
            ]:
                try:
                    field = page.locator(selector).first
                    if await field.count() == 0 or not await field.is_visible():
                        continue
                    current = await field.input_value()
                    if not current.strip():
                        await field.fill(self.login_email)
                    filled += 1
                    break
                except Exception:
                    continue

        password_value = self.account_password or self.login_password
        if password_value:
            try:
                pw_fields = page.locator("input[type='password']")
                total_pw = min(await pw_fields.count(), 3)
            except Exception:
                total_pw = 0
            for idx in range(total_pw):
                try:
                    field = pw_fields.nth(idx)
                    if not await field.is_visible():
                        continue
                    current = await field.input_value()
                    if not current.strip():
                        await field.fill(password_value)
                    filled += 1
                except Exception:
                    continue

        try:
            checkbox = page.locator("input[type='checkbox']").first
            if await checkbox.count() > 0 and await checkbox.is_visible():
                checked = False
                try:
                    checked = await checkbox.is_checked()
                except Exception:
                    checked = False
                if not checked:
                    await checkbox.check(force=True)
                    filled += 1
        except Exception:
            pass

        try:
            filled += await filler.fill_visible_fields()
        except Exception:
            pass

        clicked = await self._click_first(
            page,
            [
                "button[data-automation-id='createAccountSubmitButton']",
                "button[type='submit']:has-text('Create Account')",
                "[data-automation-id='click_filter'][aria-label='Create Account']",
                "button:has-text('Create Account')",
            ],
            timeout_ms=5000,
        )
        if clicked:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

        return opened, filled, clicked

    async def _prefer_sign_in_path(self, page: Page) -> bool:
        if not self.login_password:
            return False
        clicked = await self._click_first_with_retry(
            page,
            [
                "button[data-automation-id='signInLink']",
                "[data-automation-id='signInLink']",
                "a:has-text('Already have an account? Sign In')",
                "button:has-text('Already have an account? Sign In')",
            ],
            total_timeout_ms=2500,
            click_timeout_ms=1200,
            interval_ms=450,
        )
        if clicked:
            return True

        return await self._click_first_with_retry(
            page,
            [
                "button[data-automation-id='utilityButtonSignIn']",
                "[data-automation-id='utilityButtonSignIn']",
                "div:has-text('Create Account') a:has-text('Sign In')",
                "section:has-text('Create Account') a:has-text('Sign In')",
                "div:has-text('Already have an account') a:has-text('Sign In')",
            ],
            total_timeout_ms=2500,
            click_timeout_ms=1200,
            interval_ms=450,
        )

    async def _follow_redirect_notice(self, page: Page) -> str:
        current = page.url.lower()
        if "eightfold.ai/r?" not in current and "click_apply_to_job" not in current:
            return ""

        target_url = ""
        try:
            link = page.locator("a[href^='http']").first
            if await link.count() > 0:
                target_url = str(await link.get_attribute("href") or "")
        except Exception:
            target_url = ""

        clicked = await self._click_first(page, ["a[href^='http']"], timeout_ms=10000)
        if not clicked:
            return target_url

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=90000)
        except Exception:
            pass
        return target_url

    async def _extract_direct_apply_url(self, page: Page) -> str:
        selectors = [
            "meta[name='search-job-apply-url']",
            "meta[name='search-job-mobile-apply-url']",
            "a[data-apply-url]",
            "a[data-selector-name='job-apply-link']",
            "a.job-apply",
            "a.ajd_btn__apply",
            "a[href*='myworkdayjobs.com'][href*='/apply']",
        ]
        attr_order = ("content", "data-apply-url", "href")
        base_url = page.url

        for selector in selectors:
            try:
                nodes = page.locator(selector)
                total = min(await nodes.count(), 8)
            except Exception:
                continue

            for idx in range(total):
                node = nodes.nth(idx)
                for attr in attr_order:
                    try:
                        raw = str(await node.get_attribute(attr) or "").strip()
                    except Exception:
                        raw = ""
                    if not raw or raw.lower().startswith("javascript:"):
                        continue
                    candidate = urljoin(base_url, raw)
                    lowered = candidate.lower()
                    if "myworkdayjobs.com" in lowered and "/apply" in lowered:
                        return candidate

        return ""

    async def _start_workday_flow(self, page: Page) -> bool:
        if "myworkdayjobs.com" not in page.url:
            return False

        try:
            await page.wait_for_selector("text=Apply Manually", timeout=8000)
        except Exception:
            pass

        await self._accept_cookies(page)

        if (
            "applymanually" in page.url.lower()
            or "autofillwithresume" in page.url.lower()
            or "review" in page.url.lower()
        ):
            return False

        clicked = await self._click_first_with_retry(
            page,
            APPLY_START_SELECTORS,
            total_timeout_ms=9000,
            click_timeout_ms=2500,
        )
        if clicked:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=90000)
            except Exception:
                pass
            await page.wait_for_timeout(500)
        return clicked

    async def _choose_application_page(self, source_page: Page) -> tuple[Page, str]:
        direct_apply = await self._extract_direct_apply_url(source_page)
        if direct_apply:
            try:
                await source_page.goto(direct_apply, wait_until="domcontentloaded", timeout=90000)
                await source_page.wait_for_timeout(300)
                await self._start_workday_flow(source_page)
                return source_page, direct_apply
            except Exception:
                pass

        popup_page: Page | None = None
        clicked_apply = False

        try:
            async with source_page.expect_popup(timeout=3500) as popup_info:
                clicked_apply = await self._click_first_with_retry(
                    source_page,
                    APPLY_SELECTORS,
                    total_timeout_ms=9000,
                    click_timeout_ms=2500,
                )
            popup_page = await popup_info.value
        except PlaywrightTimeoutError:
            if not clicked_apply:
                clicked_apply = await self._click_first_with_retry(
                    source_page,
                    APPLY_SELECTORS,
                    total_timeout_ms=9000,
                    click_timeout_ms=2500,
                )
        except Exception:
            if not clicked_apply:
                clicked_apply = await self._click_first_with_retry(
                    source_page,
                    APPLY_SELECTORS,
                    total_timeout_ms=9000,
                    click_timeout_ms=2500,
                )

        active = popup_page if popup_page else source_page
        try:
            await active.wait_for_load_state("domcontentloaded", timeout=90000)
        except Exception:
            pass
        await active.wait_for_timeout(400)

        apply_target = await self._follow_redirect_notice(active)
        if not apply_target and "myworkdayjobs.com" in active.url.lower() and "/apply/" in active.url.lower():
            apply_target = active.url
        if not apply_target:
            direct_apply = await self._extract_direct_apply_url(active)
            if direct_apply:
                apply_target = direct_apply
                try:
                    await active.goto(direct_apply, wait_until="domcontentloaded", timeout=90000)
                    await active.wait_for_timeout(300)
                except Exception:
                    pass

        await self._start_workday_flow(active)
        if not apply_target and "myworkdayjobs.com" in active.url.lower() and "/apply/" in active.url.lower():
            apply_target = active.url
        return active, apply_target

    @staticmethod
    def _recover_open_page(context: BrowserContext, fallback: Page | None = None) -> Page | None:
        if fallback is not None:
            try:
                if not fallback.is_closed():
                    return fallback
            except Exception:
                pass

        for candidate in reversed(context.pages):
            try:
                if not candidate.is_closed():
                    return candidate
            except Exception:
                continue
        return None

    async def _run_application_steps(
        self,
        page: Page,
        context: BrowserContext,
        apply_target_url: str = "",
        max_steps: int = 16,
    ) -> tuple[int, bool, Page | None, list[str]]:
        filler = FormFiller(page, self.profile)
        filled_total = 0
        idle_rounds = 0
        refresh_attempted = False
        started_at = time.time()
        current_page: Page | None = page
        trace: list[str] = []
        step_fill_attempts: dict[str, int] = {}
        signin_attempts = 0
        signin_cooldown = 0
        create_account_fallback_used = False

        for step_index in range(1, max_steps + 1):
            current_page = self._recover_open_page(context, current_page)
            if current_page is None:
                break
            if current_page is not page:
                page = current_page
                filler = FormFiller(page, self.profile)

            if time.time() - started_at > self.max_role_seconds:
                break

            try:
                current_url = page.url.lower()
            except Exception:
                current_url = ""
            active_step = await self._current_apply_step(page)
            step_url = current_url.split("?", 1)[0]
            step_key = f"{(active_step or 'unknown').strip().lower()}|{step_url}"
            step_norm = (active_step or "").strip().lower()
            if len(trace) < 80:
                trace.append(f"iter={step_index}; step={active_step or 'unknown'}; url={current_url}")
            logger.info(
                "apply-step iter=%s step=%s url=%s",
                step_index,
                active_step or "unknown",
                current_url,
            )

            if apply_target_url and (
                "/my-applications" in current_url
                or "/candidate/home" in current_url
                or "/candidate/profile" in current_url
                or "/userhome" in current_url
            ):
                try:
                    await page.goto(apply_target_url, wait_until="domcontentloaded", timeout=90000)
                    filler = FormFiller(page, self.profile)
                    continue
                except Exception:
                    pass

            try:
                await page.wait_for_timeout(700)
            except Exception:
                current_page = self._recover_open_page(context)
                if current_page is None:
                    break
                page = current_page
                filler = FormFiller(page, self.profile)
                continue

            await self._accept_cookies(page)
            logged_in_now = await self._is_logged_in(page)
            if logged_in_now:
                self._session_authenticated = True
            elif self._session_authenticated:
                # Keep session sticky unless an explicit sign-in prompt is on screen.
                if not await self._is_signin_prompt_visible(page):
                    logged_in_now = True

            if await self._has_runtime_page_error(page):
                if len(trace) < 80:
                    trace.append(f"iter={step_index}; action=runtime_error_page_detected")
                if apply_target_url:
                    try:
                        await page.goto(apply_target_url, wait_until="domcontentloaded", timeout=90000)
                        await page.wait_for_timeout(500)
                        await self._start_workday_flow(page)
                        filler = FormFiller(page, self.profile)
                        if len(trace) < 80:
                            trace.append(f"iter={step_index}; action=runtime_error_recovered")
                        continue
                    except Exception:
                        pass
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=90000)
                    await page.wait_for_timeout(500)
                    await self._start_workday_flow(page)
                    filler = FormFiller(page, self.profile)
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=runtime_error_reload")
                    continue
                except Exception:
                    pass

            sign_in_prompt_visible = await self._is_signin_prompt_visible(page)
            if self._session_authenticated and sign_in_prompt_visible:
                if await self._close_signin_prompt(page):
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=signin_modal_closed")
                    continue

            should_attempt_login = self.login_password and not logged_in_now
            create_account_step = should_attempt_login and (
                await self._is_create_account_step(page) or await self._is_create_account_form_visible(page)
            )
            if create_account_step:
                if signin_cooldown > 0:
                    signin_cooldown -= 1
                    try:
                        await page.wait_for_timeout(700)
                    except Exception:
                        pass
                    continue
                signin_attempts += 1
                if signin_attempts > 2:
                    if not create_account_fallback_used and self.account_password:
                        opened_create, filled_create, clicked_create = await self._attempt_create_account_fallback(
                            page, filler
                        )
                        if len(trace) < 80:
                            trace.append(
                                "iter="
                                f"{step_index}; action=create_account_fallback_from_limit; "
                                f"opened={opened_create}; fill={filled_create}; clicked={clicked_create}"
                            )
                        if clicked_create:
                            create_account_fallback_used = True
                            signin_attempts = 0
                            idle_rounds = 0
                            continue
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=signin_attempt_limit_reached")
                    break
                opened_signin = await self._prefer_sign_in_path(page)
                await page.wait_for_timeout(350)
                filled_signin = await self._fill_signin_credentials(page)
                if len(trace) < 80:
                    trace.append(
                        f"iter={step_index}; action=signin_fill; count={filled_signin}; opened={opened_signin}"
                    )
                clicked_signin = False
                if filled_signin > 0:
                    clicked_signin = await self._click_modal_signin(page)
                if not clicked_signin:
                    await self._prefer_sign_in_path(page)
                    await page.wait_for_timeout(250)
                    filled_retry = await self._fill_signin_credentials(page)
                    if len(trace) < 80:
                        trace.append(
                            f"iter={step_index}; action=signin_fill_retry; count={filled_retry}"
                        )
                    if filled_retry > 0:
                        clicked_signin = await self._click_modal_signin(page)
                if clicked_signin:
                    signin_cooldown = 2
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=signin_click; result=clicked")
                    idle_rounds = 0
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                if await self._has_auth_error(page):
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=signin_auth_error")
                    if not create_account_fallback_used and self.account_password:
                        opened_create, filled_create, clicked_create = await self._attempt_create_account_fallback(
                            page, filler
                        )
                        if len(trace) < 80:
                            trace.append(
                                "iter="
                                f"{step_index}; action=create_account_fallback_on_auth_error; "
                                f"opened={opened_create}; fill={filled_create}; clicked={clicked_create}"
                            )
                        if clicked_create:
                            create_account_fallback_used = True
                            signin_attempts = 0
                            idle_rounds = 0
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                            except Exception:
                                pass
                            continue
                    break
                # Never fill create-account fields when login credentials are configured.
                continue

            sign_in_prompt_visible = should_attempt_login and sign_in_prompt_visible
            if sign_in_prompt_visible:
                if signin_cooldown > 0:
                    signin_cooldown -= 1
                    try:
                        await page.wait_for_timeout(700)
                    except Exception:
                        pass
                    continue
                signin_attempts += 1
                if signin_attempts > 2:
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=signin_prompt_attempt_limit_reached")
                    break
                filled_total += await self._fill_signin_credentials(page)
                signed_in = await self._click_modal_signin(page)
                if signed_in:
                    signin_cooldown = 2
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=signin_click; result=clicked")
                    idle_rounds = 0
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                continue

            if await self._is_oracle_email_verification_step(page):
                accepted = await self._accept_oracle_legal_disclaimer(page)
                if accepted:
                    filled_total += accepted
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=oracle_disclaimer_accept; count={accepted}")
                if await self._requires_human_verification(page):
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=human_verification_required")
                    break

            # Avoid spending fill attempts on non-form landing pages.
            is_application_host = any(
                host in current_url
                for host in (
                    "myworkdayjobs.com",
                    "oraclecloud.com",
                    "eightfold.ai",
                    "greenhouse.io",
                    "lever.co",
                )
            )
            if not is_application_host and "/job/" in current_url:
                direct_apply = await self._extract_direct_apply_url(page)
                if direct_apply:
                    try:
                        await page.goto(direct_apply, wait_until="domcontentloaded", timeout=90000)
                        filler = FormFiller(page, self.profile)
                        await page.wait_for_timeout(1000)
                        idle_rounds = 0
                        if len(trace) < 80:
                            trace.append(
                                f"iter={step_index}; action=goto_direct_apply; target={direct_apply}"
                            )
                        continue
                    except Exception:
                        pass

            non_form_steps = {"careers", "candidate home", "search for jobs"}
            if step_norm in non_form_steps or (not step_norm and "/apply" in current_url):
                clicked_apply = await self._click_first(page, APPLY_SELECTORS, timeout_ms=5000)
                if clicked_apply:
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=apply_click; result=clicked")
                    idle_rounds = 0
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    continue

                started = await self._start_workday_flow(page)
                if started:
                    if len(trace) < 80:
                        trace.append(f"iter={step_index}; action=start_workday_flow; result=true")
                    idle_rounds = 0
                    continue

            has_validation_errors = await self._has_validation_errors(page)
            fill_budget = 3 if has_validation_errors else 1
            fill_attempts = step_fill_attempts.get(step_key, 0)
            filled_now = 0
            if fill_attempts < fill_budget:
                filled_now = await filler.fill_visible_fields()
                filled_total += filled_now
                step_fill_attempts[step_key] = fill_attempts + 1
                if filled_now > 0 and len(trace) < 80:
                    trace.append(
                        f"iter={step_index}; action=fill; count={filled_now}; pass={fill_attempts + 1}; budget={fill_budget}"
                    )
            elif len(trace) < 80:
                trace.append(
                    f"iter={step_index}; action=fill_skip; pass={fill_attempts}; budget={fill_budget}"
                )

            if not self.auto_submit:
                break

            if await self._has_auth_error(page):
                if len(trace) < 80:
                    trace.append(f"iter={step_index}; action=auth_error")
                break

            if await self._click_first(page, SUBMIT_SELECTORS, timeout_ms=3500):
                if len(trace) < 80:
                    trace.append(f"iter={step_index}; action=submit_click; result=clicked")
                logger.info("submit-clicked url=%s", current_url)
                await page.wait_for_timeout(500)
                return filled_total, True, page, trace

            moved = await self._click_page_footer_next(page)
            if not moved:
                moved = await self._click_first(page, CONTINUE_SELECTORS, timeout_ms=3500)
            if moved:
                if len(trace) < 80:
                    trace.append(f"iter={step_index}; action=continue_click; result=clicked")
                idle_rounds = 0
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                continue

            started = await self._start_workday_flow(page)
            if started:
                if len(trace) < 80:
                    trace.append(f"iter={step_index}; action=start_workday_flow; result=true")
                idle_rounds = 0
                continue

            if filled_now > 0:
                idle_rounds = 0
                continue

            idle_rounds += 1
            if idle_rounds >= 2 and not refresh_attempted and apply_target_url:
                refresh_attempted = True
                try:
                    await page.goto(apply_target_url, wait_until="domcontentloaded", timeout=90000)
                    await page.wait_for_timeout(500)
                    filler = FormFiller(page, self.profile)
                    idle_rounds = 0
                    continue
                except Exception:
                    pass
            if idle_rounds >= 3:
                break

        return filled_total, False, page, trace

    def _result(
        self,
        job: JobPosting,
        status: str,
        message: str,
        screenshot_path: str = "",
    ) -> ApplicationResult:
        return ApplicationResult(
            job_id=job.id,
            job_url=job.url,
            company=job.company,
            title=job.title,
            status=status,
            message=message,
            screenshot_path=screenshot_path,
        )

    def _persist_results(self, results: list[ApplicationResult]) -> Path:
        payload = [
            {
                "job_id": item.job_id,
                "job_url": item.job_url,
                "company": item.company,
                "title": item.title,
                "status": item.status,
                "message": item.message,
                "screenshot_path": item.screenshot_path,
            }
            for item in results
        ]

        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_file = self.output_dir / f"application_results_{stamp}.json"
        out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return out_file

    async def _apply_single_job(self, p, context, is_persistent, job) -> ApplicationResult | None:
        logger.info("apply-start company=%s title=%s url=%s", job.company, job.title, job.url)
        if is_persistent and context.pages:
            page: Page | None = await context.new_page()
        else:
            page = await context.new_page()
        active_page: Page | None = page

        screenshot_file = self.output_dir / (
            f"{self._sanitize_filename(job.company)}_"
            f"{self._sanitize_filename(job.title)}.png"
        )
        html_snapshot_file = self.output_dir / (
            f"{self._sanitize_filename(job.company)}_"
            f"{self._sanitize_filename(job.title)}.html"
        )

        result_obj = None
        try:
            await page.goto(job.url, wait_until="domcontentloaded", timeout=90000)
            current_url_lower = page.url.lower()
            direct_workday_apply = (
                "myworkdayjobs.com" in current_url_lower and "/apply/" in current_url_lower
            ) or (
                "myworkdayjobs.com" in job.url.lower() and "/apply/" in job.url.lower()
            )

            if direct_workday_apply:
                active_page = page
                apply_target_url = page.url if "myworkdayjobs.com" in page.url.lower() else job.url
                await self._start_workday_flow(active_page)
            else:
                active_page, apply_target_url = await self._choose_application_page(page)
            logger.info("apply-target company=%s title=%s target=%s", job.company, job.title, apply_target_url or active_page.url)

            if await self._is_already_applied_page(active_page):
                message = f"already_applied=True; final_url={active_page.url}"
                result_obj = self._result(
                    job=job,
                    status="already_applied",
                    message=message,
                    screenshot_path=str(screenshot_file),
                )
                return result_obj

            filled_fields, submitted, maybe_page, trace = await self._run_application_steps(
                active_page,
                context,
                apply_target_url=apply_target_url,
            )
            if maybe_page is not None:
                active_page = maybe_page
            auth_error = await self._has_auth_error(active_page)

            if self.auto_submit:
                if submitted:
                    status = "applied"
                elif await self._is_already_applied_page(active_page):
                    status = "already_applied"
                elif await self._requires_human_verification(active_page):
                    status = "requires_human"
                elif auth_error:
                    status = "failed_auth"
                else:
                    status = "filled_no_submit"
                message = (
                    f"filled_fields={filled_fields}; auto_submit={self.auto_submit}; "
                    f"submitted={submitted}; auth_error={auth_error}; "
                    f"final_url={active_page.url}"
                )
            else:
                status = "filled"
                message = f"filled_fields={filled_fields}; auto_submit=False; final_url={active_page.url}"

            if trace:
                compact_trace = " | ".join(trace[:18])
                message = f"{message}; trace={compact_trace}"

            if status != "applied":
                try:
                    content = await active_page.content()
                    html_snapshot_file.write_text(content, encoding="utf-8")
                    message = f"{message}; html_snapshot={html_snapshot_file}"
                except Exception:
                    pass

            try:
                await active_page.screenshot(path=str(screenshot_file), full_page=True)
            except Exception:
                pass
            result_obj = self._result(
                job=job,
                status=status,
                message=message,
                screenshot_path=str(screenshot_file),
            )
            logger.info(
                "apply-end company=%s title=%s status=%s submitted=%s filled=%s",
                job.company,
                job.title,
                status,
                submitted if self.auto_submit else False,
                filled_fields,
            )

        except (Error, Exception) as exc:
            logger.exception("apply-failed company=%s title=%s error=%s", job.company, job.title, exc)
            result_obj = self._result(
                job=job,
                status="failed",
                message=str(exc),
                screenshot_path=str(screenshot_file),
            )
        finally:
            to_close: list[Page] = []
            if active_page is not None:
                to_close.append(active_page)
            if page is not None and page != active_page:
                to_close.append(page)

            for candidate in to_close:
                try:
                    # In persistent context, dont close the very first page if we want session intact
                    # but here we opened new page, so it's safe to close
                    if not candidate.is_closed():
                        await candidate.close()
                except Exception:
                    pass

        return result_obj

    async def apply(self, jobs: list[RankedJob]) -> tuple[list[ApplicationResult], Path]:
        results: list[ApplicationResult] = []
        logger.info("apply-batch-start jobs=%s headless=%s auto_submit=%s", len(jobs), self.headless, self.auto_submit)

        async with async_playwright() as p:
            browser = None
            is_persistent = self.session_dir is not None
            if self.session_dir is not None:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(self.session_dir),
                    headless=self.headless,
                )
            else:
                browser = await p.chromium.launch(headless=self.headless)
                context = await browser.new_context()

            try:
                context.set_default_timeout(15000)
                context.set_default_navigation_timeout(60000)
            except Exception:
                pass

            # Keep a single deterministic flow per shared browser session.
            total = len(jobs)
            for idx, ranked in enumerate(jobs, start=1):
                logger.info(
                    "apply-batch-progress index=%s/%s company=%s title=%s",
                    idx,
                    total,
                    ranked.job.company,
                    ranked.job.title,
                )
                one = await self._apply_single_job(p, context, is_persistent, ranked.job)
                if one is not None:
                    results.append(one)

            await context.close()
            if browser is not None:
                await browser.close()

        result_file = self._persist_results(results)
        logger.info("apply-batch-end results=%s file=%s", len(results), result_file)
        return results, result_file
