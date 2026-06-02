import unittest
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import localflow_app as lf


class LearningSafetyTests(unittest.TestCase):
    def test_allows_unambiguous_weird_token_correction(self):
        self.assertTrue(lf._is_safe_learned_correction("enos", "n8n"))

    def test_blocks_common_contractions_and_punctuated_common_words(self):
        self.assertFalse(lf._is_safe_learned_correction("it's", "this"))
        self.assertFalse(lf._is_safe_learned_correction("bro.", "broken thing."))

    def test_blocks_context_phrase_corrections(self):
        self.assertFalse(lf._is_safe_learned_correction("no more", "normal"))
        self.assertFalse(lf._is_safe_learned_correction("asian markdown", "agent markdown"))


class CustomVocabularyTests(unittest.TestCase):
    def test_corrects_glm_acronym_confusion_when_in_dictionary(self):
        text = lf._apply_custom_words("newest within GOM and newest within GPT", ["GLM", "GPT"])
        self.assertEqual(text, "newest within GLM and newest within GPT")

    def test_corrects_owntracks_location_ping_domain_phrase(self):
        text = lf._apply_domain_phrase_corrections(
            "send a one time location hang from the only tracks app that we want you to do right now."
        )
        self.assertEqual(
            text,
            "send a one-time location ping from the OwnTracks app. Is that what you want me to do right now?"
        )

    def test_does_not_change_topics_to_anthropic(self):
        text = lf._apply_custom_words("a full list of all the topics", ["Anthropic"])
        self.assertEqual(text, "a full list of all the topics")

    def test_formats_two_spoken_questions_as_numbered_list(self):
        raw = (
            "Okay, so two questions. Question number one. How often is GPS syncing right now? "
            "So if I'm moving, let's say right now I'm going to gym in 15 minutes. Would it update? "
            "And I finish gym in one hour and come back, would it update? And second question. "
            "Can both Jarvis, VM and Mac, can both request and ping where I am? when I ask the question like, what is near me?"
        )
        self.assertEqual(
            lf._format_spoken_question_list(raw),
            "Okay, so two questions:\n\n"
            "1) How often is GPS syncing right now? So if I'm moving, let's say right now I'm going to gym in 15 minutes. "
            "Would it update? And I finish gym in one hour and come back, would it update?\n\n"
            "2) Can both Jarvis, VM and Mac, can both request and ping where I am? When I ask the question like, what is near me?"
        )

    def test_corrects_request_and_pen_location_ping(self):
        text = lf._apply_domain_phrase_corrections("can both request and pen where I am?")
        self.assertEqual(text, "can both request and ping where I am?")

    def test_corrects_pre_workout_now_question(self):
        text = lf._apply_domain_phrase_corrections("Should I take your workout down?")
        self.assertEqual(text, "Should I take pre-workout now?")

    def test_removes_stray_home_before_owntracks_ping(self):
        text = lf._apply_domain_phrase_corrections("Second, home OwnTracks ping.")
        self.assertEqual(text, "Second, OwnTracks ping.")
        text = lf._apply_domain_phrase_corrections("Second, home tracks ping.")
        self.assertEqual(text, "Second, OwnTracks ping.")

    def test_detects_long_punctuation_regression(self):
        raw = (
            "Okay so the reason being is I'm trying to think if we can create a lightweight pad just like Rick. "
            "I really like the Rick Design where it's always just on my screen and I can see it clearly. "
            "So what do you think about that? But also I do like the part where it shows me token usage."
        )
        cleaned = (
            "Okay so the reason being is I'm trying to think if we can create a lightweight pad just like Rick "
            "I really like the Rick Design where it's always just on my screen and I can see it clearly "
            "So what do you think about that But also I do like the part where it shows me token usage"
        )
        self.assertTrue(lf._is_punctuation_regression(raw, cleaned))

    def test_recording_watchdog_does_not_recover_active_long_recording(self):
        self.assertFalse(
            lf._recording_watchdog_should_recover(
                elapsed_sec=63,
                frame_count=68000,
                last_frame_count=67000,
                seconds_since_audio_progress=1,
                has_stream=True,
            )
        )

    def test_recording_watchdog_recovers_dead_stream_after_timeout(self):
        self.assertTrue(
            lf._recording_watchdog_should_recover(
                elapsed_sec=63,
                frame_count=68000,
                last_frame_count=68000,
                seconds_since_audio_progress=16,
                has_stream=True,
            )
        )

    def test_recording_watchdog_aborts_absurdly_long_recording(self):
        self.assertTrue(lf._recording_watchdog_should_abort_oversize(901))
        self.assertFalse(lf._recording_watchdog_should_abort_oversize(899))

    def test_oversize_audio_is_trimmed_before_whisper(self):
        audio = np.arange(lf.SAMPLE_RATE * 1000, dtype=np.float32)
        trimmed, was_trimmed, original_dur = lf._trim_oversize_audio_for_processing(audio)
        self.assertTrue(was_trimmed)
        self.assertAlmostEqual(original_dur, 1000.0, places=1)
        self.assertEqual(len(trimmed), lf.SAMPLE_RATE * lf._OVERSIZE_AUDIO_RECOVERY_SEC)
        self.assertEqual(float(trimmed[0]), float(audio[-len(trimmed)]))

    def test_normal_length_audio_is_not_trimmed_before_whisper(self):
        audio = np.arange(lf.SAMPLE_RATE * 30, dtype=np.float32)
        trimmed, was_trimmed, original_dur = lf._trim_oversize_audio_for_processing(audio)
        self.assertFalse(was_trimmed)
        self.assertAlmostEqual(original_dur, 30.0, places=1)
        self.assertEqual(len(trimmed), len(audio))

    def test_plain_actually_is_not_treated_as_self_correction(self):
        text = "I don't want to get it and buy it and it actually also matters."
        self.assertEqual(lf._apply_self_corrections(text), text)

    def test_wait_no_self_correction_still_works(self):
        text = "Send it to John, wait no, send it to Sarah."
        self.assertEqual(lf._apply_self_corrections(text), "Send it to Sarah.")

    def test_corrects_critical_thinking_brainstorm_instruction(self):
        text = (
            "But I was outside early at the gym and since I cannot. "
            "You don't have to fully agree. You might be wrong. "
            "So use your own people thinking and tell me what to do. "
            "Execution not yet, rank stone only."
        )
        self.assertEqual(
            lf._apply_domain_phrase_corrections(text),
            "But I was outside early at the gym and seems that it cannot. "
            "You don't have to fully agree. He might be wrong. "
            "So use your own critical thinking and tell me what to do. "
            "Execution not yet, brainstorm only."
        )

    def test_does_not_change_openai_to_openclaw(self):
        text = lf._apply_custom_words("use my OpenAI account", ["OpenClaw", "OpenAI"])
        self.assertEqual(text, "use my OpenAI account")

    def test_snippets_do_not_replace_inside_words(self):
        text = lf._apply_snippets("use my OpenAI account and CC should help", {"CC": "Claude Code"})
        self.assertEqual(text, "use my OpenAI account and Claude Code should help")

    def test_corrects_codex_login_context(self):
        text = (
            "then I have to sign out and sign back in to use my OpenAI account "
            "to make sure it is using my monthly token. every time when I lock in "
            "is not using my API to lock into codex"
        )
        self.assertEqual(
            lf._apply_domain_phrase_corrections(text),
            "then I have to sign out and sign back in to use my OpenAI account "
            "to make sure it is using my monthly token. every time when I log in "
            "is not using my API to log into codex"
        )

    def test_corrects_phase_number_planning_context(self):
        text = (
            "Why the fuck design the bot on the face 3 or face 4 "
            "are you fucking confused"
        )
        self.assertEqual(
            lf._apply_domain_phrase_corrections(text),
            "Why the fuck design the bot on the phase 3 or phase 4 "
            "are you fucking confused"
        )

    def test_does_not_change_local_like_project_to_local_flow(self):
        text = lf._apply_custom_words(
            "clean up whatever that was only local like project only",
            ["Local Flow"],
        )
        self.assertEqual(text, "clean up whatever that was only local like project only")

    def test_still_corrects_cloud_code_to_claude_code(self):
        text = lf._apply_custom_words("Cloud Code should handle this", ["Claude Code"])
        self.assertEqual(text, "Claude Code should handle this")

    def test_corrects_local_like_project_and_all_good_closing(self):
        text = (
            "Okay, so maybe clean up whatever that was only local like project only. "
            "If not, then oh good."
        )
        self.assertEqual(
            lf._apply_domain_phrase_corrections(text),
            "Okay, so maybe clean up whatever that was only local project only. "
            "If not, then all good."
        )

    def test_does_not_change_could_be_to_claude_be(self):
        text = lf._apply_custom_words("I don't know if that could be an issue", ["claude"])
        self.assertEqual(text, "I don't know if that could be an issue")

    def test_does_not_change_could_code_to_claude_code(self):
        text = lf._apply_custom_words("it could code this feature", ["Claude Code"])
        self.assertEqual(text, "it could code this feature")

    def test_does_not_change_call_to_claude(self):
        text = lf._apply_custom_words("we need a call with them", ["claude"])
        self.assertEqual(text, "we need a call with them")

    def test_still_corrects_cloud_code_to_claude_code_after_common_word_guard(self):
        text = lf._apply_custom_words("Cloud Code should audit it", ["Claude Code"])
        self.assertEqual(text, "Claude Code should audit it")


class FormattingQualityTests(unittest.TestCase):
    def test_collapses_random_llm_linebreaks_inside_prose(self):
        text = (
            "So you almost fixed it. It's not perfect.\n\n"
            "As you can see, it's not completely matching,\n"
            "but sort of acceptable. My question is how can we make sure continually\n"
            "this doesn't drift\n"
            "and always matching.\n\n"
            "I need this to be stable\n"
            "and don't need me to keep fixing it."
        )
        self.assertEqual(
            lf._normalize_prose_line_breaks(text),
            "So you almost fixed it. It's not perfect.\n\n"
            "As you can see, it's not completely matching, but sort of acceptable. "
            "My question is how can we make sure continually this doesn't drift "
            "and always matching.\n\n"
            "I need this to be stable and don't need me to keep fixing it."
        )

    def test_repairs_long_runon_at_strong_transition_markers(self):
        text = (
            "Instead of making a bot to find a job by itself and make money daily "
            "without human in the loop, would it make more sense the bot to actually "
            "find jobs but it's high ticket job meaning each ticket is minimum let's "
            "say a few thousand dollars three thousand five thousand dollars ten "
            "thousand eight thousand dollars who knows and then the human basically "
            "verify it making sure the bot is working so it's not 100% bot working "
            "but the human in the loop for this job but basically Claude Code or "
            "Cloak Dax is doing all the lifting."
        )
        repaired = lf._repair_long_runons(text)
        self.assertIn("who knows. And then the human", repaired)
        self.assertIn("this job. But basically Claude Code", repaired)
        self.assertLess(lf._longest_sentence_word_count(repaired), 70)

    def test_money_normalizer_does_not_sum_adjacent_amounts(self):
        text = (
            "each ticket is minimum let's say a few thousand dollars three thousand "
            "five thousand dollars ten thousand eight thousand dollars who knows"
        )
        normalized = lf._normalize_numbers(text)
        self.assertNotIn("$8000", normalized)
        self.assertNotIn("$18000", normalized)
        self.assertIn("three thousand five thousand dollars", normalized)
        self.assertIn("ten thousand eight thousand dollars", normalized)

    def test_money_normalizer_still_converts_simple_amount(self):
        self.assertEqual(lf._normalize_numbers("minimum five thousand dollars"), "minimum $5000")


if __name__ == "__main__":
    unittest.main()
