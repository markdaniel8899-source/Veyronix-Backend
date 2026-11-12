"""Regression tests for the simulation depth and two-way conversation.

None of these tests call the LLM: they cover the cast expansion, the
interaction planning, the conversation prompt and the thread storage.
"""

import sqlite3

import pytest

from app.config import Config
from app.services.local_memory import LocalMemory
from app.services.oasis_profile_generator import OasisProfileGenerator
from app.services.simulation_runner import SimulationRunner


GRAPH_ENTITIES = [
    {
        "uuid": "e1",
        "name": "Aisha Khan",
        "type": "Person",
        "summary": "Founder of the company",
        "attributes": {"occupation": "CEO"},
        "graph_id": "graph_x",
    },
    {
        "uuid": "e2",
        "name": "Meridian Research",
        "type": "Organization",
        "summary": "Analyst firm",
        "attributes": {},
        "graph_id": "graph_x",
    },
]


@pytest.fixture()
def generator(monkeypatch):
    # The cast expansion does not use the LLM, but the generator checks the
    # key in its constructor. A dummy value keeps the test independent of
    # the local .env file.
    monkeypatch.setattr(Config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
    return OasisProfileGenerator(graph_id="graph_x")


def test_default_rounds_is_at_least_twenty():
    assert Config.OASIS_DEFAULT_MAX_ROUNDS >= 20


def test_cast_expands_to_the_target_size(generator):
    cast = generator.expand_entity_cast(GRAPH_ENTITIES, target_count=50)

    assert len(cast) == 50
    # The graph entities are kept and lead the cast.
    assert cast[0]["name"] == "Aisha Khan"
    assert cast[1]["name"] == "Meridian Research"


def test_cast_is_not_expanded_when_it_is_already_large_enough(generator):
    entities = [
        {"uuid": f"e{i}", "name": f"Entity {i}", "type": "Person",
         "summary": "s", "attributes": {}}
        for i in range(80)
    ]

    assert len(generator.expand_entity_cast(entities, target_count=50)) == 80


def test_every_cast_member_is_fully_described(generator):
    cast = generator.expand_entity_cast(GRAPH_ENTITIES, target_count=50)

    for member in cast:
        attributes = member["attributes"]
        assert member["name"]
        assert member["stance"] in {"supportive", "opposing", "neutral"}
        assert attributes["platform"]
        assert attributes["tone"]
        assert attributes["profession"]
        assert attributes["region"]
        assert attributes["age_band"]
        assert attributes["perspective"]


def test_cast_names_are_unique(generator):
    cast = generator.expand_entity_cast(GRAPH_ENTITIES, target_count=50)

    assert len({member["name"] for member in cast}) == len(cast)


def test_stance_mix_is_thirty_forty_thirty(generator):
    cast = generator.expand_entity_cast(GRAPH_ENTITIES, target_count=50)
    stances = [member["stance"] for member in cast]

    assert stances.count("supportive") == 15
    assert stances.count("opposing") == 20
    assert stances.count("neutral") == 15


def test_cast_is_diverse(generator):
    cast = generator.expand_entity_cast(GRAPH_ENTITIES, target_count=50)

    assert len({m["attributes"]["profession"] for m in cast}) >= 10
    assert len({m["attributes"]["region"] for m in cast}) >= 5
    assert len({m["attributes"]["age_band"] for m in cast}) >= 4
    assert len({m["attributes"]["platform"] for m in cast}) >= 3


def test_cast_is_project_generic(generator):
    cast = generator.expand_entity_cast(GRAPH_ENTITIES, target_count=50)

    assert "ATLAS" not in str(cast).upper()
    assert "LEARNING OS" not in str(cast).upper()


def test_agent_starts_a_post_without_history():
    agent = {"id": 0, "name": "Amina", "stance": "supportive"}

    interaction, parent = SimulationRunner._plan_interaction(agent, [], "twitter")

    assert parent is None
    assert interaction in {"CREATE_POST", "CREATE_COMMENT"}


def test_agent_answers_another_agent(monkeypatch):
    monkeypatch.setattr(Config, "SIMULATION_REPLY_RATIO", 1.0)
    monkeypatch.setattr(
        "app.services.simulation_runner.random.random", lambda: 0.5
    )
    agent = {"id": 0, "name": "Amina", "stance": "supportive"}
    conversation = [
        {
            "post_id": "p1",
            "parent_post_id": "",
            "agent_id": 1,
            "agent_name": "Bruno",
            "platform": "twitter",
            "round_num": 1,
            "interaction_type": "CREATE_POST",
            "stance": "opposing",
            "content": "This plan is unaffordable.",
        }
    ]

    interaction, parent = SimulationRunner._plan_interaction(agent, conversation, "twitter")

    assert parent is not None
    assert parent["agent_id"] == 1
    assert interaction in {
        "REPLY", "COUNTER_ARGUMENT", "AGREEMENT", "QUESTION", "QUOTE_POST",
    }


def test_disagreeing_agent_answers_with_a_counter_argument(monkeypatch):
    monkeypatch.setattr(Config, "SIMULATION_REPLY_RATIO", 1.0)
    monkeypatch.setattr("app.services.simulation_runner.random.choice", lambda items: items[0])
    monkeypatch.setattr(
        "app.services.simulation_runner.random.random", lambda: 0.5
    )
    agent = {"id": 0, "name": "Amina", "stance": "supportive"}
    conversation = [
        {
            "post_id": "p1",
            "parent_post_id": "",
            "agent_id": 1,
            "agent_name": "Bruno",
            "platform": "twitter",
            "round_num": 1,
            "interaction_type": "CREATE_POST",
            "stance": "opposing",
            "content": "This plan is unaffordable.",
        }
    ]

    interaction, _ = SimulationRunner._plan_interaction(agent, conversation, "twitter")

    assert interaction in {"COUNTER_ARGUMENT", "REPLY"}


def test_prompt_contains_the_conversation_context():
    agent = {
        "id": 0,
        "name": "Amina",
        "stance": "supportive",
        "tone": "analytical",
        "bio": "A student",
        "persona": "A student",
        "platform": "twitter",
        "profession": "student",
        "region": "Europe",
        "perspective": "A student point of view.",
    }
    conversation = [
        {
            "post_id": "p1",
            "parent_post_id": "",
            "agent_id": 1,
            "agent_name": "Bruno",
            "platform": "reddit",
            "round_num": 1,
            "interaction_type": "CREATE_POST",
            "stance": "opposing",
            "content": "This plan is unaffordable.",
        }
    ]

    prompt = SimulationRunner._build_action_prompt(
        agent=agent,
        config={"simulation_requirement": "Falcon Logistics entering South America"},
        platform="twitter",
        round_num=3,
        simulated_hour=14,
        shock_text="",
        interaction_type="COUNTER_ARGUMENT",
        parent_post=conversation[0],
        conversation=conversation,
        total_rounds=20,
    )

    # The prompt is built from the project context, never from a fixed topic.
    assert "Falcon Logistics entering South America" in prompt
    assert "ATLAS" not in prompt.upper()
    # The agent sees the previous conversation and the post it answers.
    assert "This plan is unaffordable." in prompt
    assert "PREVIOUS CONVERSATION CONTEXT" in prompt
    # The persona behaviour is part of the prompt.
    assert "supportive" in prompt
    assert "analytical" in prompt
    # The round and the simulated hour are visible.
    assert "Round 3/20" in prompt
    assert "Simulated Hour 14:00" in prompt
    # The agent is asked to reply, not to monologue.
    assert "counter-argument" in prompt.lower()
    assert "Output ONLY the post content" in prompt


def test_interactions_store_the_thread(tmp_path):
    memory = LocalMemory(str(tmp_path / "memory.db"))
    memory.add_interaction(1, "0", "CREATE_POST", "first post", post_id="p1")
    memory.add_interaction(
        1, "1", "REPLY", "answering you",
        parent_post_id="p1", interaction_type="REPLY", post_id="p2",
    )

    rows = memory.get_recent_interactions()
    assert len(rows) == 2
    reply = next(row for row in rows if row["interaction_type"] == "REPLY")
    assert reply["parent_post_id"] == "p1"
    assert reply["post_id"] == "p2"
    root = next(row for row in rows if row["post_id"] == "p1")
    assert root["parent_post_id"] == ""


def test_old_database_is_migrated(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE interactions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "round_num INT NOT NULL,"
        "agent_id TEXT NOT NULL,"
        "action TEXT NOT NULL,"
        "content TEXT NOT NULL,"
        "timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    memory = LocalMemory(str(db_path))
    memory.add_interaction(
        2, "3", "REPLY", "migrated", parent_post_id="pX", post_id="pY",
    )

    rows = memory.get_recent_interactions()
    assert rows
    assert rows[0]["parent_post_id"] == "pX"
    assert rows[0]["post_id"] == "pY"
