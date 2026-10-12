"""Source-specific descriptions for members with stored long-form excerpts.

Only the three prose fields contribute to the displayed profile word count.
These observations do not establish a recurring interview behavior or predict
private meetings. Source metadata preserves the distinction between recorded
speech, public self-report, and the user's uncorroborated secondhand report.
"""


OVERRIDES = {
    "Steve Scalise": {
        "p1": (
            "Steve Scalise's recorded interviews provide concrete examples of how he describes working relationships. "
            "In a December 2023 POLITICO interview, he discussed conversations with colleagues during the contest for "
            "Speaker and his decision against forcing a floor vote. Describing his relationship with Mike Johnson, "
            "he emphasized years of acquaintance, previous work together, friendship, and trust. These were his accounts "
            "of particular political relationships after becoming House Majority Leader."
        ),
        "p2": (
            "An earlier interview offers a different context. On The Takeout in February 2020, the host asked about "
            "his recovery from the 2017 shooting. Scalise thanked him and explained physical therapy, relearning balance, "
            "and walking without crutches. The stored response lasts about 71 seconds. The host initiated that personal "
            "exchange; the recording therefore shows Scalise answering a personal question, rather than initiating one. "
            "It predates his January 2023 move into the Majority Leader role."
        ),
        "p3": (
            "A separate secondhand report supplied for this profile says Scalise asks people about incidental subjects, "
            "including football, before substantive discussion. Its date and frequency are unknown, and the audio does "
            "not confirm it. The current collection contains four stored response excerpts across three interviews, "
            "totaling roughly six minutes. These examples support specific descriptions of what he said. A recurring "
            "habit of initiating social conversation remains unconfirmed under the requirement for matching observations "
            "in two independent interviews with different settings."
        ),
        "sources": [
            {
                "type": "stored_longform_turns",
                "source_id": "politico-asd1904021436",
                "title": "Steve Scalise reveals what’s really happened since McCarthy’s fall",
                "date": "2023-12-01",
                "url": "https://www.politico.com/news/2023/12/01/what-steve-scalise-wont-forget-00129546",
                "turn_ids": ["steve-scalise-politico-01", "steve-scalise-politico-02"],
                "source_era": "CURRENT_ROLE_CONDITIONED",
                "behavior_status": "PROVISIONAL_SPECIFIC_OBSERVATIONS",
            },
            {
                "type": "stored_longform_turn",
                "source_id": "0ec06fa55008",
                "title": "Whip It Good: Congressman Steve Scalise",
                "date": "2020-02-07",
                "url": "https://podcasts.apple.com/us/podcast/whip-it-good-congressman-steve-scalise/id1195899470?i=1000464891036&uo=4",
                "turn_id": "steve-scalise-takeout-prerole-01",
                "source_era": "PRE_CURRENT_LEADERSHIP_ROLE",
                "behavior_status": "PROVISIONAL_SPECIFIC_OBSERVATION",
            },
            {
                "type": "uncorroborated_secondhand_report",
                "report_id": "steve-scalise-user-secondhand-20260904-01",
                "reported_at": "2026-09-04",
                "observation_date": None,
                "frequency": None,
                "audio_confirmed": False,
                "interview_evidence_count": 0,
            },
        ],
    },
    "Mike Johnson": {
        "p1": (
            "Mike Johnson's clearest stored long-form example comes from a friendly Better for America interview "
            "recorded in March 2022, before he became Speaker in October 2023. Asked a broad question about Russia's "
            "war against Ukraine, he gave an answer lasting just over two minutes. He began with the principle "
            "of peace through strength, then connected the conflict to other international risks before describing "
            "limits on American involvement."
        ),
        "p2": (
            "In that answer, Johnson argued that perceived weakness encouraged aggression. He moved from Russia and "
            "Ukraine to China and Taiwan, North Korea, and Iran. He then described congressional applause for "
            "Volodymyr Zelensky, saying that helping Ukraine involved a national interest and a moral responsibility. "
            "He also stated that the United States should avoid outright war with Russia and sending American "
            "troops. The sequence is observable in this answer: a general principle, several examples, and an "
            "explicit boundary. It remains a provisional observation from one interview."
        ),
        "p3": (
            "The wider archive contains nearly 200,000 attributed interview words and about 31 minutes of accepted "
            "audio from NPR and Sunday programs. Those excerpts provide additional public-speaking material, but do "
            "not show that this sequence recurs in complete responses. His tracked legislative involvement spans "
            "a range of issue areas that scores low on the archive's comparison measure. Neither that record nor the 2022 exchange "
            "establishes how he handles an unfamiliar question in a private meeting. A second independent interview "
            "in a different setting is needed before describing a recurring communication pattern."
        ),
        "sources": [
            {
                "type": "stored_longform_turn",
                "source_id": "381a280129f9",
                "title": "#109 Peace Through Strength with Congressman Mike Johnson",
                "recorded_at": "2022-03-24",
                "published": "2022-04-01",
                "url": "https://podcasts.apple.com/us/podcast/109-peace-through-strength-with-congressman-mike-johnson/id1541390793?i=1000556420266&uo=4",
                "turn_id": "mike-johnson-better-for-america-01",
                "source_era": "PRE_CURRENT_LEADERSHIP_ROLE",
                "behavior_status": "PROVISIONAL_SPECIFIC_OBSERVATION",
            },
            {
                "type": "local_evidence_fields",
                "path": "data/members/member_narrative_evidence_v4.json",
                "member": "Mike Johnson",
                "fields": [
                    "behavior.policy_breadth",
                    "speech_profile.general_word_habits.raw_text_observations",
                    "speech_profile.general_audio_habits",
                    "speech_profile.by_setting",
                ],
            },
        ],
    },
    "Alex Padilla": {
        "p1": (
            "Alex Padilla's tracked legislative issue-coverage measure ranks above the sample's midpoint. His share "
            "of cross-party ties and cosponsorship places him near the top of the covered sample, while his combined "
            "voting-participation and bill-progress measure is also relatively high. These describe recorded legislative "
            "activity. In a March 2026 Congressional Record statement, he said he keeps a photograph of John Lewis "
            "in his office and finds it a daily source of inspiration."
        ),
        "p2": (
            "Two stored interview excerpts show him discussing different parts of his experience. In The New York "
            "Times' The Interview in October 2025, Padilla described the events preceding his confrontation at a "
            "Los Angeles press conference. He explained his attempts to obtain information, his scheduled briefing, "
            "the delay, and why he spoke up. In Making Los Angeles that July, he described being born in Panorama "
            "City, his family's move to Pacoima, and the schools he attended. The first excerpt follows a public "
            "dispute; the second concerns his upbringing."
        ),
        "p3": (
            "The collection currently preserves two stored response excerpts from these interviews, totaling about three "
            "minutes. The wider archive has approximately 33,000 attributed interview words and 32 minutes of accepted "
            "audio across ten episodes. That provides useful public context, but the two selected responses have "
            "not established the same communication behavior across different settings. His discussion of events "
            "and personal history should therefore remain specific examples. It does not yet establish a recurring "
            "answer structure, a preference for personal conversation, or how he responds during private meetings."
        ),
        "sources": [
            {
                "type": "stored_longform_turn",
                "source_id": "Bu66zRWS13c",
                "title": "Sen. Alex Padilla Says His Viral Moment ‘Validated’ Democrats’ Warnings",
                "date": "2025-10-04",
                "url": "https://www.youtube.com/watch?v=Bu66zRWS13c",
                "requested_url": "https://podcasts.apple.com/us/podcast/the-interview-sen-alex-padilla-says-his-viral-moment/id1200361736?i=1000730070252",
                "turn_id": "alex-padilla-nyt-01",
                "behavior_status": "PROVISIONAL_SPECIFIC_OBSERVATION",
            },
            {
                "type": "stored_longform_turn",
                "source_id": "making-la-8c20db13",
                "title": "Alex Padilla: From Pacoima to Power",
                "date": "2025-07-16",
                "url": "https://www.lisc.org/los-angeles/regional-stories/lisc-la-making-los-angeles-podcast-host-john-jones-iii-in-conversation-with-senator-alex-padilla/",
                "turn_id": "alex-padilla-making-la-01",
                "behavior_status": "PROVISIONAL_SPECIFIC_OBSERVATION",
            },
            {
                "type": "public_self_report",
                "source": "Congressional Record",
                "date": "2026-03-19",
                "locator": "CREC-2026-03-19",
                "claim": "Padilla said he keeps a photograph of John Lewis in his office and finds it inspiring.",
            },
        ],
    },
    "Maxine Waters": {
        "p1": (
            "Maxine Waters' tracked legislative issue-coverage measure ranks above the middle of the covered "
            "sample. Her combined voting-participation and bill-progress measure is also above "
            "the sample's midpoint. Her interview collection includes public discussions of political participation "
            "and democratic institutions. The strongest specific examples currently stored come from Tavis Smiley "
            "in August 2026 and Our Body Politic in January 2021; they address different events several years apart."
        ),
        "p2": (
            "Given the final word in the Tavis Smiley conversation, Waters urged listeners to express their opinions, "
            "organize, challenge decisions, and make sacrifices. She discussed threats she saw to Black political "
            "representation through redistricting. In the earlier Our Body Politic exchange, she discussed Congress "
            "continuing to certify electoral votes after the attack on the Capitol. She described completing that "
            "work as a signal against intimidation and emphasized continuing the institution's responsibilities. "
            "These are her stated arguments in those conversations. They do not establish how she receives an "
            "argument from someone else."
        ),
        "p3": (
            "The two stored response excerpts total approximately two and a half minutes. A broader collection "
            "contains about 48,000 attributed interview words and 42 minutes of accepted audio from House-floor "
            "and NPR recordings. Those settings involve different audiences and speaking opportunities. The "
            "selected podcast passages supply concrete examples of her public language, but a recurring conversational "
            "pattern remains unconfirmed under the two-interview standard. Her appeals for public action should "
            "not be converted into assumptions about personal warmth, emotional state, or behavior in an individual meeting."
        ),
        "sources": [
            {
                "type": "stored_longform_turn",
                "source_id": "spreaker-74239308",
                "title": "U.S. Rep. Maxine Waters & Dr. Carl L. Hart",
                "date": "2026-08-18",
                "url": "https://www.spreaker.com/episode/u-s-rep-maxine-waters-dr-carl-l-hart--74239308",
                "requested_url": "https://podcasts.apple.com/us/podcast/u-s-rep-maxine-waters-dr-carl-l-hart/id1609823559?i=1000783986824",
                "turn_id": "maxine-waters-tavis-01",
                "behavior_status": "PROVISIONAL_SPECIFIC_OBSERVATION",
            },
            {
                "type": "stored_longform_turn",
                "source_id": "our-body-politic-dffe1298",
                "title": "Congresswoman Maxine Waters on How the Democratic Party Moves Forward",
                "date": "2021-01-22",
                "url": "https://our-body-politic.simplecast.com/episodes/january-22-2021-congresswoman-maxine-waters-on-how-the-democratic-party-moves-forward-black-and-indigenous-strategists-demanding-more-from-the-government-and-the-biggest-challenges-facing-the-biden-harris-administration-hIS5APCa/transcript",
                "turn_id": "maxine-waters-our-body-politic-01",
                "behavior_status": "PROVISIONAL_SPECIFIC_OBSERVATION",
            },
            {
                "type": "local_evidence_fields",
                "path": "data/members/member_narrative_evidence_v4.json",
                "member": "Maxine Waters",
                "fields": [
                    "behavior.policy_breadth",
                    "behavior.legislative_diligence",
                    "speech_profile.general_word_habits.raw_text_observations",
                    "speech_profile.general_audio_habits",
                ],
            },
        ],
    },
}
