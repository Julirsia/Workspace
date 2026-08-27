from pathlib import Path
import xml.etree.ElementTree as ET

root = Path("futo")

# ---------------------------------------------------------------------------
# 1. Android-visible subtypes: copy ReteKey's proven two-subtype structure.
#    No custom subtype IDs, no implicit-subtype override, no explicit enabling,
#    and no bidirectional switchInputMethod loop.
# ---------------------------------------------------------------------------
method = root / "java/res/xml/method.xml"
method.write_text(
    '''<?xml version="1.0" encoding="utf-8"?>

<input-method xmlns:android="http://schemas.android.com/apk/res/android"
    android:settingsActivity="org.futo.inputmethod.latin.uix.settings.SettingsActivity"
    android:isDefault="true"
    android:supportsSwitchingToNextInputMethod="true"
    android:supportsInlineSuggestions="true"
    android:suppressesSpellChecker="true">
    <subtype
        android:languageTag="ko-KR"
        android:imeSubtypeLocale="ko_KR"
        android:imeSubtypeMode="keyboard"
        android:isAsciiCapable="false" />
    <subtype
        android:languageTag="en-US"
        android:imeSubtypeLocale="en_US"
        android:imeSubtypeMode="keyboard"
        android:isAsciiCapable="true" />
</input-method>
''',
    encoding="utf-8",
)

# Give this build a fresh package ID, so Samsung/Android cannot reuse the
# cached single-subtype registration from earlier test APKs.
gradle = root / "build.gradle"
gradle_text = gradle.read_text(encoding="utf-8")
old_suffix = '            applicationIdSuffix ".unstable"\n'
new_suffix = '            applicationIdSuffix ".retekeyhardware"\n'
if new_suffix not in gradle_text:
    if old_suffix not in gradle_text:
        raise SystemExit("Unstable applicationIdSuffix anchor not found")
    gradle_text = gradle_text.replace(old_suffix, new_suffix, 1)
gradle.write_text(gradle_text, encoding="utf-8")

# Make the fresh package unmistakable in Android's keyboard chooser.
app_name = root / "java/unstable/res/values/strings-appname.xml"
app_text = app_name.read_text(encoding="utf-8")
app_text = app_text.replace(
    "FUTO Keyboard [Dev Build]",
    "FUTO Keyboard [ReteKey HW]",
)
app_text = app_text.replace(
    "FUTO Spell Checker [Dev Build]",
    "FUTO Spell Checker [ReteKey HW]",
)
app_text = app_text.replace(
    "FUTO Keyboard Settings [Dev Build]",
    "FUTO Keyboard Settings [ReteKey HW]",
)
app_text = app_text.replace(
    "FUTO Spell Checker Settings [Dev Build]",
    "FUTO Spell Checker Settings [ReteKey HW]",
)
app_name.write_text(app_text, encoding="utf-8")

# ---------------------------------------------------------------------------
# 2. ReteKey-style system subtype -> FUTO internal subtype bridge.
#
# Android owns Ctrl+Space and switches the two static subtypes. FUTO keeps its
# own richer subtype records (locale + KeyboardLayoutSet). We translate only
# in that one direction and keep an explicit hardware Korean-mode flag.
# ---------------------------------------------------------------------------
latin = root / "java/src/org/futo/inputmethod/latin/LatinIME.kt"
text = latin.read_text(encoding="utf-8")


def add_import(anchor: str, addition: str) -> None:
    global text
    if addition.strip() in text:
        return
    if anchor not in text:
        raise SystemExit(f"Import anchor not found: {anchor!r}")
    text = text.replace(anchor, anchor + addition, 1)


add_import(
    "import android.view.inputmethod.InputMethodSubtype\n",
    "import android.view.inputmethod.InputMethodManager\n",
)
add_import(
    "import org.futo.inputmethod.latin.uix.setSetting\n",
    "import org.futo.inputmethod.latin.uix.setSettingBlocking\n",
)
add_import(
    "import org.futo.inputmethod.v2keyboard.isFoldableInnerDisplayAllowed\n",
    "import java.util.Locale\n",
)

bridge_marker = "private var reteKeyHardwareKoreanMode = false"
bridge_anchor = '    private var currentSubtype = ""\n'
bridge = r'''    // Physical-key language state. ReteKey keeps this separately from the
    // on-screen layout, initialises it from Android's current subtype, and
    // routes physical letters through a Dubeolsik mapper when Korean.
    @Volatile
    private var reteKeyHardwareKoreanMode = false
    private var reteKeyLegacyReady = false
    private var reteKeyPendingSystemLanguage: String? = null

    fun isReteKeyHardwareKoreanMode(): Boolean = reteKeyHardwareKoreanMode

    private fun reteKeyLanguageOf(subtype: InputMethodSubtype?): String? {
        if (subtype == null) return null
        return try {
            Subtypes.getLocale(subtype).language
        } catch (_: Throwable) {
            null
        }
    }

    private fun reteKeyConfiguredSubtype(language: String): String? {
        val preferredLayout = when (language) {
            "ko" -> "korean_dubeolsik"
            "en" -> "qwerty"
            else -> return null
        }
        val entries = getSettingBlocking(SubtypesSetting).toList()

        return entries.firstOrNull { entry ->
            try {
                val subtype = Subtypes.convertToSubtype(entry)
                Subtypes.getLocale(subtype).language == language &&
                        subtype.getExtraValueOf("KeyboardLayoutSet") == preferredLayout
            } catch (_: Throwable) {
                false
            }
        } ?: entries.firstOrNull { entry ->
            try {
                Subtypes.getLocale(Subtypes.convertToSubtype(entry)).language == language
            } catch (_: Throwable) {
                false
            }
        }
    }

    private fun reteKeyEnsureSubtype(language: String): String? {
        reteKeyConfiguredSubtype(language)?.let { return it }
        if (!isDirectBootUnlocked) return null

        when (language) {
            "ko" -> Subtypes.addLanguage(this, Locale.KOREA, "korean_dubeolsik")
            "en" -> Subtypes.addLanguage(this, Locale.US, "qwerty")
            else -> return null
        }
        return reteKeyConfiguredSubtype(language)
    }

    private fun reteKeyApplySystemLanguage(language: String) {
        if (language != "ko" && language != "en") return

        reteKeyHardwareKoreanMode = language == "ko"
        if (!reteKeyLegacyReady) {
            reteKeyPendingSystemLanguage = language
            return
        }

        val target = reteKeyEnsureSubtype(language) ?: return
        reteKeyPendingSystemLanguage = null

        if (getSettingBlocking(ActiveSubtype) != target) {
            setSettingBlocking(ActiveSubtype.key, target)
        }
        // Use FUTO's own rich subtype (with KeyboardLayoutSet), never the
        // plain Android static subtype. This updates the visible layout and
        // the active Korean combiner synchronously.
        changeSubtype(target)
        Log.i(
            "LatinIME",
            "ReteKey-style Android subtype -> FUTO: $language / $target"
        )
    }

    private fun reteKeySyncCurrentSystemSubtype() {
        val pending = reteKeyPendingSystemLanguage
        if (pending != null) {
            reteKeyApplySystemLanguage(pending)
            return
        }

        try {
            val imm = getSystemService(InputMethodManager::class.java)
            reteKeyLanguageOf(imm.currentInputMethodSubtype)?.let {
                reteKeyApplySystemLanguage(it)
            }
        } catch (t: Throwable) {
            Log.e("LatinIME", "Unable to read current Android IME subtype", t)
        }
    }

    override fun onCurrentInputMethodSubtypeChanged(newSubtype: InputMethodSubtype?) {
        super.onCurrentInputMethodSubtypeChanged(newSubtype)
        reteKeyLanguageOf(newSubtype)?.let {
            reteKeyApplySystemLanguage(it)
        }
    }

'''

if bridge_marker not in text:
    class_pos = text.find("class LatinIME : InputMethodServiceCompose()")
    anchor_pos = text.find(bridge_anchor)
    if class_pos < 0 or anchor_pos < class_pos:
        raise SystemExit("LatinIME currentSubtype anchor not found in LatinIME")
    text = text.replace(bridge_anchor, bridge + bridge_anchor, 1)

# Make both internal FUTO layouts exist before the service starts accepting
# system subtype changes.
default_anchor = "        Subtypes.addDefaultSubtypesIfNecessary(this)\n"
default_patch = '''        Subtypes.addDefaultSubtypesIfNecessary(this)
        if (isDirectBootUnlocked) {
            reteKeyEnsureSubtype("ko")
            reteKeyEnsureSubtype("en")
        }
'''
if default_patch not in text:
    if default_anchor not in text:
        raise SystemExit("Default subtype initialization anchor not found")
    text = text.replace(default_anchor, default_patch, 1)

# The legacy engine must exist before we translate a system subtype into a
# FUTO rich subtype.
legacy_anchor = "        latinIMELegacy.onCreate()\n"
legacy_patch = '''        latinIMELegacy.onCreate()
        reteKeyLegacyReady = true
        reteKeySyncCurrentSystemSubtype()
'''
if legacy_patch not in text:
    if legacy_anchor not in text:
        raise SystemExit("LatinIMELegacy initialization anchor not found")
    text = text.replace(legacy_anchor, legacy_patch, 1)

old_change = '''    fun changeSubtype(subtypeString: String) {
        if(currentSubtype == subtypeString) return
        currentSubtype = subtypeString

        val subtype = Subtypes.convertToSubtype(subtypeString)
        changeInputMethodSubtype(subtype)
        uixManager.updateLocale(Subtypes.getLocale(subtype))
    }
'''
new_change = '''    fun changeSubtype(subtypeString: String) {
        val subtype = Subtypes.convertToSubtype(subtypeString)
        reteKeyHardwareKoreanMode = Subtypes.getLocale(subtype).language == "ko"

        if(currentSubtype == subtypeString) return
        currentSubtype = subtypeString

        changeInputMethodSubtype(subtype)
        uixManager.updateLocale(Subtypes.getLocale(subtype))
    }
'''
if new_change not in text:
    if old_change not in text:
        raise SystemExit("LatinIME.changeSubtype anchor not found")
    text = text.replace(old_change, new_change, 1)

unlock_anchor = "        CanThrowIfDebug = true\n"
unlock_patch = '''        CanThrowIfDebug = true

        reteKeyEnsureSubtype("ko")
        reteKeyEnsureSubtype("en")
        reteKeySyncCurrentSystemSubtype()
'''
if unlock_patch not in text:
    unlock_start = text.find("    private fun onDeviceUnlocked()")
    anchor_pos = text.find(unlock_anchor, unlock_start)
    if unlock_start < 0 or anchor_pos < 0:
        raise SystemExit("onDeviceUnlocked anchor not found")
    text = text[:anchor_pos] + unlock_patch + text[anchor_pos + len(unlock_anchor):]

latin.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# 3. Physical-key mapping, adapted from ReteKey's MIT-licensed
#    DubeolsikHardwareMapper. The output is Hangul compatibility jamo, which
#    FUTO's existing KoreanCombiner already consumes.
# ---------------------------------------------------------------------------
mapper_path = root / "java/src/org/futo/inputmethod/event/ReteKeyDubeolsikHardwareMapper.java"
mapper_path.write_text(
    r'''/*
 * Physical QWERTY -> Korean Dubeolsik mapping for FUTO Keyboard.
 *
 * Mapping architecture adapted from ReteKey:
 * https://github.com/rubidus-api/retekey_apk
 *
 * MIT License
 * Copyright (c) 2026 rubidus-api
 */
package org.futo.inputmethod.event;

import android.view.KeyEvent;

import org.futo.inputmethod.latin.LatinIME;

public final class ReteKeyDubeolsikHardwareMapper {
    private ReteKeyDubeolsikHardwareMapper() {}

    private static final int SHORTCUT_META_STATES =
            KeyEvent.META_CTRL_ON
                    | KeyEvent.META_ALT_ON
                    | KeyEvent.META_META_ON
                    | KeyEvent.META_SYM_ON
                    | KeyEvent.META_FUNCTION_ON;

    public static Event remapIfNeeded(
            final Event original,
            final KeyEvent keyEvent,
            final LatinIME latinIME) {
        if (original == null || keyEvent == null || latinIME == null) {
            return original;
        }
        if (!latinIME.isReteKeyHardwareKoreanMode()) {
            return original;
        }
        if (!original.isHandled()
                || original.getEventType() != Event.EVENT_TYPE_INPUT_KEYPRESS) {
            return original;
        }

        final int normalizedMeta = KeyEvent.normalizeMetaState(keyEvent.getMetaState());
        if ((normalizedMeta & SHORTCUT_META_STATES) != 0) {
            // Ctrl/Alt/Meta shortcuts must reach the target application.
            return original;
        }

        final int mappedCodePoint = mapKeyCode(
                keyEvent.getKeyCode(),
                keyEvent.isShiftPressed());
        if (mappedCodePoint == Event.NOT_A_CODE_POINT) {
            return original;
        }

        return Event.createHardwareKeypressEvent(
                mappedCodePoint,
                keyEvent.getKeyCode(),
                original.mNextEvent,
                original.isKeyRepeat());
    }

    public static int mapKeyCode(final int keyCode, final boolean shifted) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_Q: return shifted ? 'ㅃ' : 'ㅂ';
            case KeyEvent.KEYCODE_W: return shifted ? 'ㅉ' : 'ㅈ';
            case KeyEvent.KEYCODE_E: return shifted ? 'ㄸ' : 'ㄷ';
            case KeyEvent.KEYCODE_R: return shifted ? 'ㄲ' : 'ㄱ';
            case KeyEvent.KEYCODE_T: return shifted ? 'ㅆ' : 'ㅅ';
            case KeyEvent.KEYCODE_Y: return 'ㅛ';
            case KeyEvent.KEYCODE_U: return 'ㅕ';
            case KeyEvent.KEYCODE_I: return 'ㅑ';
            case KeyEvent.KEYCODE_O: return shifted ? 'ㅒ' : 'ㅐ';
            case KeyEvent.KEYCODE_P: return shifted ? 'ㅖ' : 'ㅔ';

            case KeyEvent.KEYCODE_A: return 'ㅁ';
            case KeyEvent.KEYCODE_S: return 'ㄴ';
            case KeyEvent.KEYCODE_D: return 'ㅇ';
            case KeyEvent.KEYCODE_F: return 'ㄹ';
            case KeyEvent.KEYCODE_G: return 'ㅎ';
            case KeyEvent.KEYCODE_H: return 'ㅗ';
            case KeyEvent.KEYCODE_J: return 'ㅓ';
            case KeyEvent.KEYCODE_K: return 'ㅏ';
            case KeyEvent.KEYCODE_L: return 'ㅣ';

            case KeyEvent.KEYCODE_Z: return 'ㅋ';
            case KeyEvent.KEYCODE_X: return 'ㅌ';
            case KeyEvent.KEYCODE_C: return 'ㅊ';
            case KeyEvent.KEYCODE_V: return 'ㅍ';
            case KeyEvent.KEYCODE_B: return 'ㅠ';
            case KeyEvent.KEYCODE_N: return 'ㅜ';
            case KeyEvent.KEYCODE_M: return 'ㅡ';

            default: return Event.NOT_A_CODE_POINT;
        }
    }
}
''',
    encoding="utf-8",
)

# Include the complete MIT notice in the APK.
notice = root / "java/res/raw/retekey_mit_license.txt"
notice.parent.mkdir(parents=True, exist_ok=True)
notice.write_text(
    '''ReteKey MIT License

Copyright (c) 2026 rubidus-api

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
''',
    encoding="utf-8",
)

legacy_path = root / "java/src/org/futo/inputmethod/latin/LatinIMELegacy.java"
legacy = legacy_path.read_text(encoding="utf-8")
import_anchor = "import org.futo.inputmethod.event.HardwareKeyboardEventDecoder;\n"
mapper_import = "import org.futo.inputmethod.event.ReteKeyDubeolsikHardwareMapper;\n"
if mapper_import not in legacy:
    if import_anchor not in legacy:
        raise SystemExit("HardwareKeyboardEventDecoder import anchor not found")
    legacy = legacy.replace(import_anchor, import_anchor + mapper_import, 1)

old_decode = '''        final Event event = getHardwareKeyEventDecoder(
                keyEvent.getDeviceId()).decodeHardwareKey(keyEvent);
'''
new_decode = '''        Event event = getHardwareKeyEventDecoder(
                keyEvent.getDeviceId()).decodeHardwareKey(keyEvent);
        event = ReteKeyDubeolsikHardwareMapper.remapIfNeeded(
                event, keyEvent, (LatinIME)mInputMethodService);
'''
if new_decode not in legacy:
    if old_decode not in legacy:
        raise SystemExit("LatinIMELegacy hardware decoder anchor not found")
    legacy = legacy.replace(old_decode, new_decode, 1)

legacy_path.write_text(legacy, encoding="utf-8")

# ---------------------------------------------------------------------------
# Build-harness validation.
# ---------------------------------------------------------------------------
android_ns = "{http://schemas.android.com/apk/res/android}"
xml_root = ET.parse(method).getroot()
subtypes = list(xml_root.findall("subtype"))
assert len(subtypes) == 2
assert [s.attrib[android_ns + "imeSubtypeLocale"] for s in subtypes] == [
    "ko_KR",
    "en_US",
]
assert [s.attrib[android_ns + "isAsciiCapable"] for s in subtypes] == [
    "false",
    "true",
]
for subtype in subtypes:
    assert android_ns + "subtypeId" not in subtype.attrib
    assert android_ns + "imeSubtypeExtraValue" not in subtype.attrib
    assert android_ns + "overridesImplicitlyEnabledSubtype" not in subtype.attrib

patched_latin = latin.read_text(encoding="utf-8")
patched_legacy = legacy_path.read_text(encoding="utf-8")
mapper = mapper_path.read_text(encoding="utf-8")

assert patched_latin.count("override fun onCurrentInputMethodSubtypeChanged") == 1
assert "isReteKeyHardwareKoreanMode" in patched_latin
assert "reteKeySyncCurrentSystemSubtype()" in patched_latin
assert "setExplicitlyEnabledInputMethodSubtypes" not in patched_latin
assert "switchInputMethod(" not in patched_latin

assert patched_legacy.count("ReteKeyDubeolsikHardwareMapper.remapIfNeeded") == 1
assert mapper.count("case KeyEvent.KEYCODE_") == 26
assert "META_CTRL_ON" in mapper and "META_ALT_ON" in mapper
assert "shifted ? 'ㄲ' : 'ㄱ'" in mapper
assert "shifted ? 'ㅒ' : 'ㅐ'" in mapper
assert 'applicationIdSuffix ".retekeyhardware"' in gradle.read_text(encoding="utf-8")
assert "FUTO Keyboard [ReteKey HW]" in app_name.read_text(encoding="utf-8")

print("Patched FUTO with ReteKey-style Android subtypes and physical Dubeolsik path")
print("Fresh package: org.futo.inputmethod.latin.retekeyhardware")
print(method.read_text(encoding="utf-8"))
