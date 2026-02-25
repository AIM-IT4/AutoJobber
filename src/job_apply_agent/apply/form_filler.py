from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.async_api import ElementHandle, Locator, Page


FIELD_KEYWORDS = {
    "first_name": ["first name", "given name"],
    "last_name": ["last name", "surname", "family name"],
    "email": ["email", "e-mail"],
    "password": ["password"],
    "verify_password": [
        "verify new password",
        "verify password",
        "confirm password",
        "re-enter password",
        "repeat password",
    ],
    "phone": ["phone number", "mobile number", "telephone number"],
    "phone_type": ["phone device type", "phone type", "device type"],
    "phone_extension": ["phone extension"],
    "phone_country_code": ["country phone code", "phone country code", "country code"],
    "city": ["city", "town"],
    "state": ["state", "province", "region"],
    "country": ["country"],
    "address_line1": ["address line 1", "street address", "address 1"],
    "address_line2": ["address line 2", "apartment", "suite", "address 2"],
    "postal_code": ["postal code", "zip code", "zip", "pin code", "pincode"],
    "heard_about": ["how did you hear", "hear about us", "source"],
    "previous_worker": ["previously worked at morgan stanley", "previously worked"],
    "linkedin": ["linkedin"],
    "github": ["github"],
    "portfolio": ["portfolio", "website", "personal site"],
    "authorized": ["authorized", "eligible to work", "legally authorized"],
    "require_sponsorship": ["sponsorship", "require visa", "need visa"],
    "cover_letter_text": ["cover letter", "why are you interested", "why do you want"],
    "agree_terms": ["agree", "consent", "privacy", "terms", "conditions"],
}


class FormFiller:
    def __init__(self, page: Page, profile: dict) -> None:
        self.page = page
        self.profile = profile

        person = profile.get("person", {})
        contact = profile.get("contact", {})
        links = profile.get("links", {})
        auth = profile.get("work_authorization", {})
        raw_answers = profile.get("answers", {}) or {}
        phone_country_code_raw = str(contact.get("phone_country_code", "")).strip()
        phone_country_code_norm = phone_country_code_raw.lower()
        phone_country_code_value = phone_country_code_raw
        if phone_country_code_norm in {"india", "in", "+91", "91"}:
            phone_country_code_value = "India (+91)"
        elif phone_country_code_norm in {"united states", "us", "usa", "+1", "1"}:
            phone_country_code_value = "United States of America (+1)"
        elif not phone_country_code_value and str(contact.get("country", "")).strip().lower() == "india":
            phone_country_code_value = "India (+91)"
        previous_worker_answer = "No"
        for question, answer in raw_answers.items():
            if "previously worked" in str(question).lower():
                previous_worker_answer = str(answer)
                break

        self.values = {
            "first_name": str(person.get("first_name", "")),
            "last_name": str(person.get("last_name", "")),
            "email": str(contact.get("email", "")),
            "password": str(profile.get("account_password", "")),
            "verify_password": str(profile.get("account_password", "")),
            "phone": str(contact.get("phone", "")),
            "phone_type": str(contact.get("phone_type", "Cell")),
            "phone_extension": str(contact.get("phone_extension", "")),
            "phone_country_code": phone_country_code_value,
            "city": str(contact.get("city", "")),
            "state": str(contact.get("state", "")),
            "country": str(contact.get("country", "")),
            "address_line1": str(contact.get("address_line1", "")),
            "address_line2": str(contact.get("address_line2", "")),
            "postal_code": str(contact.get("postal_code", "")),
            "linkedin": str(links.get("linkedin", "")),
            "github": str(links.get("github", "")),
            "portfolio": str(links.get("portfolio", "")),
            "authorized": str(auth.get("authorized", "Yes")),
            "require_sponsorship": str(auth.get("require_sponsorship", "No")),
            "cover_letter_text": str(profile.get("cover_letter_text", "")),
            "agree_terms": "Yes",
            "heard_about": str(profile.get("heard_about", "LinkedIn")),
            "previous_worker": previous_worker_answer,
        }

        self.resume_path = Path(str(profile.get("resume_path", ""))).expanduser()
        self.answers = {
            str(question).lower(): str(answer)
            for question, answer in raw_answers.items()
        }
        self.answers_compact = [
            (self._compact(question), str(answer))
            for question, answer in raw_answers.items()
            if str(answer or "").strip()
        ]
        self.debug_path = Path("outputs/fill_debug.log")
        self._answered_question_keys: set[tuple[str, str]] = set()
        self._state_fallback_done = False

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()

    async def _visible_nodes(self, locator: Locator, max_items: int = 6) -> list[Locator]:
        nodes: list[Locator] = []
        try:
            total = await locator.count()
        except Exception:
            return nodes
        for idx in range(min(total, max_items)):
            node = locator.nth(idx)
            try:
                if await node.is_visible():
                    nodes.append(node)
            except Exception:
                continue
        return nodes

    async def _click_option(self, value: str) -> bool:
        cleaned = value.strip()
        if not cleaned:
            return False

        patterns = [
            re.compile(rf"^{re.escape(cleaned)}$", re.IGNORECASE),
            re.compile(re.escape(cleaned), re.IGNORECASE),
        ]
        selectors = [
            "[role='option']",
            "[data-automation-id='promptOption']",
            "p[data-automation-id='promptOption']",
            "[data-automation-id='menuItem']",
            "li[role='option']",
            "li",
        ]

        for pattern in patterns:
            for selector in selectors:
                try:
                    options = self.page.locator(selector).filter(has_text=pattern)
                except Exception:
                    continue
                for candidate in await self._visible_nodes(options, max_items=12):
                    try:
                        await candidate.click(force=True)
                        return True
                    except Exception:
                        continue
        return False

    async def _value_applied(self, element: ElementHandle, expected: str, include_nearby: bool = True) -> bool:
        target = self._normalize(expected)
        if not target:
            return False

        try:
            snapshot = await element.evaluate(
                """
                (el) => {
                  const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim();
                  const direct = 'value' in el ? clean(el.value) : '';
                  const text = clean(el.textContent);
                  const aria = clean(el.getAttribute('aria-label'));
                  const nearby = el.closest('[data-automation-id^="formField-"], [data-fkit-id], div');
                  const nearbyText = nearby ? clean(nearby.textContent) : '';
                  return { direct, text, aria, nearbyText };
                }
                """
            )
        except Exception:
            return False

        candidates = [
            self._normalize(str((snapshot or {}).get("direct", ""))),
            self._normalize(str((snapshot or {}).get("text", ""))),
            self._normalize(str((snapshot or {}).get("aria", ""))),
        ]
        if include_nearby:
            candidates.append(self._normalize(str((snapshot or {}).get("nearbyText", ""))))
        for candidate in candidates:
            if not candidate:
                continue
            if target in candidate or candidate in target:
                return True
        return False

    def _debug(self, message: str) -> None:
        if os.getenv("JOB_APPLY_DEBUG", "0").strip() not in {"1", "true", "yes"}:
            return
        try:
            self.debug_path.parent.mkdir(parents=True, exist_ok=True)
            self.debug_path.write_text("", encoding="utf-8") if not self.debug_path.exists() else None
            with self.debug_path.open("a", encoding="utf-8") as handle:
                handle.write(message.strip() + "\n")
        except Exception:
            pass

    async def _apply_custom_dropdown(self, element: ElementHandle, value: str) -> bool:
        if not value:
            return False

        if await self._value_applied(element, value, include_nearby=False):
            return False

        try:
            await element.click(force=True)
            await self.page.wait_for_timeout(200)
        except Exception:
            return False

        try:
            el_id = (await element.get_attribute("id") or "").strip()
        except Exception:
            el_id = ""
        try:
            el_name = (await element.get_attribute("name") or "").strip()
        except Exception:
            el_name = ""
        try:
            before_text = str(await element.evaluate("(el) => (el.textContent || '').replace(/\\s+/g, ' ').trim()") or "")
        except Exception:
            before_text = ""
        self._debug(
            f"custom_dropdown:start id={el_id} name={el_name} before={before_text!r} target={value!r}"
        )

        tag_name = ""
        try:
            tag_name = str(await element.evaluate("(el) => el.tagName.toLowerCase()") or "")
        except Exception:
            pass

        if tag_name in {"input", "textarea"}:
            try:
                await element.fill(value)
                await self.page.wait_for_timeout(200)
            except Exception:
                pass

        for _ in range(6):
            if await self._click_option(value):
                await self.page.wait_for_timeout(150)
                if await self._value_applied(element, value):
                    self._debug(
                        f"custom_dropdown:option_click_success id={el_id} name={el_name} target={value!r}"
                    )
                    return True
            await self.page.wait_for_timeout(180)

        try:
            has_popup = (await element.get_attribute("aria-haspopup") or "").lower()
        except Exception:
            has_popup = ""
        if has_popup == "listbox":
            try:
                await element.press("ArrowDown")
                await self.page.wait_for_timeout(180)
            except Exception:
                pass
            try:
                await self.page.keyboard.type(value, delay=20)
                await self.page.wait_for_timeout(200)
            except Exception:
                pass

        try:
            await element.press("Enter")
            await self.page.wait_for_timeout(150)
        except Exception:
            pass

        try:
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(120)
        except Exception:
            pass

        applied = await self._value_applied(element, value)
        try:
            after_text = str(await element.evaluate("(el) => (el.textContent || '').replace(/\\s+/g, ' ').trim()") or "")
        except Exception:
            after_text = ""
        self._debug(
            f"custom_dropdown:end id={el_id} name={el_name} after={after_text!r} target={value!r} applied={applied}"
        )
        return applied

    async def _text_already_matches(self, element: ElementHandle, value: str, input_type: str = "") -> bool:
        if not value:
            return False
        try:
            current = await element.input_value()
        except Exception:
            return False

        current_text = str(current or "").strip()
        if not current_text:
            return False

        if input_type in {"tel", "number"}:
            current_digits = re.sub(r"[^0-9]", "", current_text)
            target_digits = re.sub(r"[^0-9]", "", value)
            return bool(current_digits) and current_digits == target_digits

        return self._normalize(current_text) == self._normalize(value)

    async def _select_already_matches(self, element: ElementHandle, value: str) -> bool:
        target = self._normalize(value)
        if not target:
            return False
        try:
            selected = await element.evaluate(
                """
                (el) => {
                  const idx = typeof el.selectedIndex === 'number' ? el.selectedIndex : -1;
                  if (idx < 0 || !el.options || !el.options[idx]) return { value: '', label: '' };
                  const opt = el.options[idx];
                  return {
                    value: (opt.value || '').trim(),
                    label: (opt.textContent || '').replace(/\\s+/g, ' ').trim(),
                  };
                }
                """
            )
        except Exception:
            return False

        selected_value = self._normalize(str((selected or {}).get("value", "")))
        selected_label = self._normalize(str((selected or {}).get("label", "")))
        for candidate in (selected_value, selected_label):
            if not candidate:
                continue
            if target == candidate or target in candidate or candidate in target:
                return True
        return False

    async def _radio_group_label(self, element: ElementHandle) -> str:
        try:
            label = await element.evaluate(
                """
                (el) => {
                  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                  const fieldset = el.closest('fieldset');
                  if (fieldset) {
                    const legend = fieldset.querySelector('legend');
                    if (legend) {
                      const text = clean(legend.textContent);
                      if (text) return text;
                    }
                  }

                  const container = el.closest('[data-automation-id^="formField-"], [data-fkit-id], div');
                  if (container) {
                    const question = container.querySelector('label');
                    if (question) {
                      const text = clean(question.textContent);
                      if (text) return text;
                    }
                  }

                  return '';
                }
                """
            )
            return str(label or "").strip()
        except Exception:
            return ""

    async def _apply_radio(self, element: ElementHandle, value: str) -> bool:
        normalized_target = self._as_bool(value)
        option_value = self._normalize(await element.get_attribute("value") or "")
        option_label = self._normalize(await self._field_label(element))

        should_select = False
        if option_value in {"true", "yes", "1", "y"}:
            should_select = normalized_target
        elif option_value in {"false", "no", "0", "n"}:
            should_select = not normalized_target
        elif "yes" in option_label:
            should_select = normalized_target
        elif "no" in option_label:
            should_select = not normalized_target

        if not should_select:
            return False

        try:
            if await element.is_checked():
                return False
        except Exception:
            pass

        try:
            await element.check(force=True)
            return True
        except Exception:
            pass

        try:
            input_id = await element.get_attribute("id") or ""
            if input_id:
                await self.page.locator(f"label[for='{input_id}']").first.click(force=True)
                return True
        except Exception:
            pass

        try:
            await element.click(force=True)
            return True
        except Exception:
            return False

    @staticmethod
    def _question_terms(question: str) -> list[str]:
        text = re.sub(r"\s+", " ", str(question or "")).strip()
        if not text:
            return []

        terms = [text]
        if "?" in text:
            prefix = text.split("?", 1)[0].strip()
            if len(prefix) >= 12:
                terms.append(prefix)
        if len(text) > 56:
            terms.append(text[:56].strip())
        if len(text) > 36:
            terms.append(text[:36].strip())

        seen: set[str] = set()
        unique: list[str] = []
        for term in terms:
            lowered = term.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            unique.append(term)
        return unique

    async def _container_locators(self, question_locator: Locator) -> list[Locator]:
        containers: list[Locator] = []
        paths = [
            "xpath=ancestor::*[@data-automation-id='formField'][1]",
            "xpath=ancestor::*[self::fieldset or @role='group'][1]",
            "xpath=ancestor::div[1]",
            "xpath=ancestor::div[2]",
            "xpath=ancestor::div[3]",
        ]
        for path in paths:
            try:
                candidate = question_locator.locator(path)
                if await candidate.count() > 0:  
                    containers.append(candidate.first)
            except Exception:
                continue
        return containers

    async def _click_answer_inside(self, container: Locator, answer: str) -> bool:
        target = answer.strip()
        if not target:
            return False

        target_lower = target.lower()
        if target_lower in {"yes", "no"}:
            try:
                radio = container.locator(f"input[type='radio'][value='{target_lower}']").first
                if await radio.count() > 0:
                    await radio.check(force=True)
                    return True
            except Exception:
                pass

            try:
                aria = container.locator(f"[role='radio'][aria-label='{target.title()}']").first
                if await aria.count() > 0 and await aria.is_visible():
                    await aria.click(force=True)
                    return True
            except Exception:
                pass

        for exact in (True, False):
            try:
                options = container.get_by_text(target, exact=exact)
            except Exception:
                continue
            for candidate in await self._visible_nodes(options, max_items=8):
                try:
                    await candidate.click(force=True)
                    return True
                except Exception:
                    continue

        return False

    async def _select_inside(self, container: Locator, answer: str) -> bool:
        triggers = [
            "input[role='combobox']",
            "[role='combobox']",
            "button[aria-haspopup='listbox']",
            "div[role='button'][aria-haspopup='listbox']",
            "[data-automation-id='wd-SelectInput']",
            "[data-automation-id='promptOption']",
            "select",
        ]

        for selector in triggers:
            try:
                candidates = container.locator(selector)
            except Exception:
                continue

            for node in await self._visible_nodes(candidates, max_items=4):
                try:
                    handle = await node.element_handle()
                    if handle is None:
                        continue

                    tag_name = str(await handle.evaluate("(el) => el.tagName.toLowerCase()") or "").lower()
                    if tag_name == "select":
                        if await self._apply_select(handle, answer):
                            return True
                        continue

                    if await self._apply_custom_dropdown(handle, answer):
                        return True
                except Exception:
                    continue

        return False

    async def _apply_question_driven_answers(self) -> int:
        applied = 0

        for question, answer in self.answers.items():
            clean_answer = str(answer or "").strip()
            if not clean_answer:
                continue
            cache_key = (self._compact(question), self._compact(clean_answer))
            if cache_key in self._answered_question_keys:
                continue

            handled = False
            for term in self._question_terms(question):
                try:
                    prompts = self.page.get_by_text(term, exact=False)
                except Exception:
                    continue

                for prompt in await self._visible_nodes(prompts, max_items=4):
                    for container in await self._container_locators(prompt):
                        if await self._click_answer_inside(container, clean_answer):
                            applied += 1
                            handled = True
                            break
                        if await self._select_inside(container, clean_answer):
                            applied += 1
                            handled = True
                            break
                    if handled:
                        break
                if handled:
                    break
            if handled:
                self._answered_question_keys.add(cache_key)

        return applied

    async def _apply_state_fallback(self) -> int:
        if self._state_fallback_done:
            return 0
        state_value = self.values.get("state", "").strip()
        if not state_value:
            return 0

        labels = ["state", "province", "region"]
        for label in labels:
            try:
                prompts = self.page.get_by_text(label, exact=False)
            except Exception:
                continue
            for prompt in await self._visible_nodes(prompts, max_items=5):
                for container in await self._container_locators(prompt):
                    if await self._select_inside(container, state_value):
                        self._state_fallback_done = True
                        return 1

        return 0

    async def _field_label(self, element: ElementHandle) -> str:
        label = await element.evaluate(
            """
            (el) => {
              const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const direct = clean(el.getAttribute('aria-label'));
              if (direct) {
                const generic = direct.toLowerCase();
                if (!/^select one( required)?$/.test(generic)) {
                  return direct;
                }
              }

              const id = el.getAttribute('id');
              if (id) {
                const byFor = document.querySelector(`label[for="${id}"]`);
                if (byFor) {
                  const text = clean(byFor.textContent);
                  if (text) return text;
                }
              }

              const wrappedLabel = el.closest('label');
              if (wrappedLabel) {
                const text = clean(wrappedLabel.textContent);
                if (text) return text;
              }

              const nearby = el.closest('[data-qa], [data-automation-id], div, section');
              if (nearby) {
                const labelInContainer = nearby.querySelector('label');
                if (labelInContainer) {
                  const text = clean(labelInContainer.textContent);
                  if (text) return text;
                }
              }

              const formField = el.closest('[data-automation-id^="formField-"]');
              if (formField) {
                let text = clean(formField.textContent);
                if (text) {
                  text = text.replace(/\\bSelect One\\b/gi, ' ');
                  text = text.replace(/Error:[^$]*/gi, ' ');
                  text = clean(text);
                  if (text) return text;
                }
              }

              const placeholder = clean(el.getAttribute('placeholder'));
              if (placeholder) return placeholder;

              return clean(el.getAttribute('name')) || clean(el.getAttribute('id'));
            }
            """
        )
        return str(label or "").strip()

    @staticmethod
    def _as_bool(value: str) -> bool:
        return value.strip().lower() in {"yes", "true", "1", "y"}

    @staticmethod
    def _keyword_matches(normalized_label: str, keyword: str) -> bool:
        token = keyword.strip().lower()
        if not token:
            return False
        if " " in token:
            return token in normalized_label
        return re.search(rf"\b{re.escape(token)}\b", normalized_label) is not None

    def _default_answer_for_prompt(self, label: str) -> str:
        normalized = self._normalize(label)
        if not normalized:
            return ""

        if "how did you hear" in normalized or "hear about us" in normalized:
            return self.values.get("heard_about", "")
        if "previously worked at morgan stanley" in normalized:
            return self.values.get("previous_worker", "No")
        if "legally authorized" in normalized or "authorized to work" in normalized:
            return self.values.get("authorized", "Yes")
        if "sponsor" in normalized or "sponsorship" in normalized or "visa" in normalized:
            return self.values.get("require_sponsorship", "No")
        if any(
            token in normalized
            for token in (
                "prevent you taking up employment",
                "require an adjustment",
                "accommodation",
                "kpmg",
                "investigation",
                "disciplined",
                "cautioned",
                "dismissed",
                "misconduct",
                "disciplinary action",
                "government official",
                "close associate",
                "barclays stakeholder",
                "politically exposed",
                "related to",
                "referred or recommended",
            )
        ):
            return "No"
        if any(
            token in normalized
            for token in (
                "happy to continue",
                "are you happy to continue",
                "consent",
                "agree",
                "criminal records",
                "address verification",
                "academic qualifications",
                "employment dates",
                "employer references",
            )
        ):
            return "Yes"
        return ""

    def _answer_for_label(self, label: str, field_id: str = "", field_name: str = "") -> str:
        normalized = self._normalize(label)
        compact = self._compact(label)
        field_meta = self._compact(f"{field_id} {field_name}")
        field_id_l = str(field_id or "").strip().lower()
        field_name_l = str(field_name or "").strip().lower()

        # Workday phone fields can share container text; prioritize exact ids/names first.
        if (
            "phonenumber--phonetype" in field_id_l
            or field_name_l == "phonetype"
            or field_id_l.endswith("--phonetype")
        ):
            return self.values.get("phone_type", "")
        if (
            "phonenumber--countryphonecode" in field_id_l
            or "countryphonecode" in field_id_l
            or field_name_l == "countryphonecode"
        ):
            return self.values.get("phone_country_code", "")
        if (
            "phonenumber--extension" in field_id_l
            or field_name_l == "extension"
            or field_id_l.endswith("--extension")
        ):
            return self.values.get("phone_extension", "")
        if (
            "phonenumber--phonenumber" in field_id_l
            or field_name_l == "phonenumber"
            or field_id_l.endswith("--phonenumber")
        ):
            return self.values.get("phone", "")

        if "phone extension" in normalized or ("phone" in field_meta and "extension" in field_meta):
            return self.values.get("phone_extension", "")
        if (
            "phone number" in normalized
            or ("phone" in field_meta and "number" in field_meta and "type" not in field_meta and "code" not in field_meta)
        ):
            return self.values.get("phone", "")
        if "phone device type" in normalized or (
            "phone" in field_meta and "device" in field_meta and "type" in field_meta
        ):
            return self.values.get("phone_type", "")
        if "country phone code" in normalized or (
            "phone" in field_meta and "country" in field_meta and "code" in field_meta
        ):
            return self.values.get("phone_country_code", "")
        if (
            "how did you hear" in normalized
            or "hear about us" in normalized
            or "howdidyouhear" in field_meta
        ):
            return self.values.get("heard_about", "")
        if " state" in f" {normalized}" or "province" in normalized or "region" in normalized:
            if "united states" not in normalized:
                return self.values.get("state", "")

        prompt_answer = self._default_answer_for_prompt(label)
        if prompt_answer:
            return prompt_answer

        for question, answer in self.answers.items():
            if question in normalized or normalized in question:
                return answer
        for question_compact, answer in self.answers_compact:
            if not question_compact or not compact:
                continue
            if (
                question_compact == compact
                or question_compact in compact
                or compact in question_compact
            ):
                return answer

            q_tokens = set(question_compact.split())
            l_tokens = set(compact.split())
            if not q_tokens or not l_tokens:
                continue
            overlap = q_tokens.intersection(l_tokens)
            overlap_ratio = len(overlap) / max(1, min(len(q_tokens), len(l_tokens)))
            if len(overlap) >= 5 and overlap_ratio >= 0.55:
                return answer

        for field, keywords in FIELD_KEYWORDS.items():
            if any(self._keyword_matches(normalized, keyword) for keyword in keywords):
                return self.values.get(field, "")
        return ""

    async def _apply_text(self, element: ElementHandle, value: str) -> bool:
        if not value:
            return False
        try:
            input_type = (await element.get_attribute("type") or "").lower()
        except Exception:
            input_type = ""

        candidate = value
        if input_type in {"tel", "number"}:
            digits = re.sub(r"[^0-9]", "", value)
            if digits:
                candidate = digits

        if await self._text_already_matches(element, candidate, input_type=input_type):
            return False

        try:
            await element.fill(candidate)
            return True
        except Exception:
            return False

    async def _clear_text_if_nonempty(self, element: ElementHandle) -> bool:
        try:
            current = await element.input_value()
        except Exception:
            return False
        if not str(current or "").strip():
            return False
        try:
            await element.fill("")
            return True
        except Exception:
            return False

    async def _apply_select(self, element: ElementHandle, value: str) -> bool:
        if not value:
            return False

        if await self._select_already_matches(element, value):
            return False

        try:
            await element.select_option(label=value)
            return True
        except Exception:
            pass

        try:
            await element.select_option(value=value)
            return True
        except Exception:
            pass

        target = value.strip().lower()
        if not target:
            return False

        try:
            options = await element.evaluate(
                """
                (el) => Array.from(el.options || []).map((opt) => ({
                  value: (opt.value || '').trim(),
                  label: (opt.textContent || '').replace(/\\s+/g, ' ').trim()
                }))
                """
            )
        except Exception:
            options = []

        best_value = ""
        for opt in options or []:
            raw_value = str(opt.get("value", "")).strip()
            raw_label = str(opt.get("label", "")).strip()
            lval = raw_value.lower()
            llbl = raw_label.lower()
            if not raw_value and not raw_label:
                continue
            if target == lval or target == llbl:
                best_value = raw_value or raw_label
                break
            if target in lval or target in llbl or lval in target or llbl in target:
                best_value = raw_value or raw_label
                break

        if best_value:
            try:
                await element.select_option(value=best_value)
                return True
            except Exception:
                try:
                    await element.select_option(label=best_value)
                    return True
                except Exception:
                    return False

        return False

    async def _apply_checkbox(self, element: ElementHandle, value: str) -> bool:
        target = self._as_bool(value)
        try:
            current = await element.is_checked()
            if target != current:
                await element.click(force=True)
                return True
            return False
        except Exception:
            return False

    async def _upload_resume_if_needed(self, element: ElementHandle) -> bool:
        if not self.resume_path.exists():
            return False

        input_type = (await element.get_attribute("type") or "").lower()
        if input_type != "file":
            return False

        try:
            await element.set_input_files(str(self.resume_path))
            return True
        except Exception:
            return False

    async def fill_visible_fields(self) -> int:
        count = 0
        try:
            fields = await self.page.query_selector_all(
                "input, textarea, select, [role='combobox'], "
                "button[aria-haspopup='listbox'], div[role='button'][aria-haspopup='listbox']"
            )
        except Exception:
            return count

        for field in fields:
            try:
                input_type = (await field.get_attribute("type") or "").lower()
                role = (await field.get_attribute("role") or "").lower()
                has_popup = (await field.get_attribute("aria-haspopup") or "").lower()
                uxi_widget_type = (await field.get_attribute("data-uxi-widget-type") or "").lower()
                uxi_multi_id = (await field.get_attribute("data-uxi-multiselect-id") or "").strip()
                field_id = (await field.get_attribute("id") or "").strip()
                field_name = (await field.get_attribute("name") or "").strip()
                is_custom_dropdown = (
                    role == "combobox"
                    or has_popup == "listbox"
                    or uxi_widget_type == "selectinput"
                    or bool(uxi_multi_id)
                )
                is_questionnaire = field_id.startswith("primaryQuestionnaire--") or (
                    field_name.startswith("adb") and has_popup == "listbox"
                )

                if not await field.is_visible() and input_type not in {"file", "radio"}:
                    if is_questionnaire:
                        self._debug(
                            f"questionnaire:skip_not_visible id={field_id} name={field_name} type={input_type} popup={has_popup}"
                        )
                    continue

                if await self._upload_resume_if_needed(field):
                    count += 1
                    continue

                tag_name = await field.evaluate("(el) => el.tagName.toLowerCase()")

                if input_type in {"hidden", "submit", "image", "reset"}:
                    continue
                if input_type == "button" and not is_custom_dropdown:
                    continue

                label = await self._field_label(field)
                if not label:
                    if is_questionnaire:
                        self._debug(
                            f"questionnaire:empty_label id={field_id} name={field_name} type={input_type}"
                        )
                    continue

                if input_type == "radio":
                    group_label = await self._radio_group_label(field)
                    value = self._answer_for_label(
                        group_label or label,
                        field_id=field_id,
                        field_name=field_name,
                    )
                else:
                    value = self._answer_for_label(
                        label,
                        field_id=field_id,
                        field_name=field_name,
                    )
                if not value:
                    is_phone_extension = (
                        "phone extension" in self._normalize(label)
                        or (
                            "extension" in self._normalize(field_name)
                            and "phone" in self._normalize(field_id + " " + field_name)
                        )
                    )
                    if is_phone_extension and input_type in {"text", "tel", ""}:
                        if await self._clear_text_if_nonempty(field):
                            count += 1
                            continue
                    if is_questionnaire:
                        self._debug(
                            f"questionnaire:empty_value id={field_id} name={field_name} label={label!r}"
                        )
                    continue

                applied = False
                if tag_name == "select":
                    if await self._select_already_matches(field, value):
                        continue
                elif is_custom_dropdown:
                    if await self._value_applied(field, value, include_nearby=False):
                        continue
                elif input_type == "checkbox":
                    try:
                        if await field.is_checked() == self._as_bool(value):
                            continue
                    except Exception:
                        pass
                elif input_type == "radio":
                    normalized_target = self._as_bool(value)
                    option_value = self._normalize(await field.get_attribute("value") or "")
                    option_label = self._normalize(label)
                    should_select = False
                    if option_value in {"true", "yes", "1", "y"}:
                        should_select = normalized_target
                    elif option_value in {"false", "no", "0", "n"}:
                        should_select = not normalized_target
                    elif "yes" in option_label:
                        should_select = normalized_target
                    elif "no" in option_label:
                        should_select = not normalized_target
                    if should_select:
                        try:
                            if await field.is_checked():
                                continue
                        except Exception:
                            pass
                else:
                    if await self._text_already_matches(field, value, input_type=input_type):
                        continue

                if tag_name == "select":
                    applied = await self._apply_select(field, value)
                elif is_custom_dropdown:
                    applied = await self._apply_custom_dropdown(field, value)
                elif input_type == "radio":
                    applied = await self._apply_radio(field, value)
                elif input_type == "checkbox":
                    applied = await self._apply_checkbox(field, value)
                else:
                    applied = await self._apply_text(field, value)

                if applied:
                    count += 1
                    if input_type != "file":
                        try:
                            if field_id.startswith("primaryQuestionnaire--") or field_name.startswith("adb"):
                                self._debug(
                                    f"applied field_id={field_id} field_name={field_name} label={label!r} value={value!r}"
                                )
                        except Exception:
                            pass
                else:
                    if is_questionnaire:
                        self._debug(
                            f"questionnaire:not_applied id={field_id} name={field_name} label={label!r} value={value!r} type={input_type} popup={has_popup}"
                        )
            except Exception:
                continue

        count += await self._apply_question_driven_answers()
        count += await self._apply_state_fallback()
        return count
