import json
import time

import pytest

from app import create_app
from app.config import Config
from app.services import simulation_runner as runner_module
from app.services.entity_extractor import EntityExtractor
from app.services.local_memory import LocalMemory
from app.services.simulation_manager import SimulationManager, SimulationStatus
from app.services.simulation_runner import SimulationRunner, RunnerStatus


class FakeChatLLM:
    def __init__(self, *args, **kwargs):
        self.prompts = []

    def chat(self, messages=None, **kwargs):
        for message in messages or []:
            self.prompts.append(message.get("content", ""))
        return "This is a fake simulation post."

    def chat_json(self, messages=None, **kwargs):
        return {"entities": [], "relationships": []}


class FakeConsolidationLLM:
    def chat(self, messages=None, **kwargs):
        return "Agents debated the topic. Public sentiment shifted slightly."


def test_local_memory_graph_scoping_search_edges_and_shocks(tmp_path):
    db_path = str(tmp_path / "memory.db")
    memory = LocalMemory(db_path)

    for graph_id, names in (
        ("graph_a", ["Alice", "Acme Corp"]),
        ("graph_b", ["Bob"]),
    ):
        memory.save_agents([
            {
                "id": f"{graph_id}_{index}",
                "persona": {
                    "uuid": f"{graph_id}_{index}",
                    "name": name,
                    "type": "Person" if name != "Acme Corp" else "Organization",
                    "labels": ["Person" if name != "Acme Corp" else "Organization"],
                    "summary": f"{name} summary",
                    "attributes": {},
                    "graph_id": graph_id,
                },
                "state": "created",
            }
            for index, name in enumerate(names)
        ])

    assert [a["id"] for a in memory.list_agents("graph_a")] == [
        "graph_a_0",
        "graph_a_1",
    ]
    assert memory.count_agents("graph_a") == 2
    assert memory.count_agents("graph_b") == 1
    assert any(
        a["persona"]["name"] == "Alice"
        for a in memory.search_agents("Alice")
    )

    memory.save_edges([
        {
            "uuid": "edge_1",
            "graph_id": "graph_a",
            "source_node_uuid": "graph_a_0",
            "target_node_uuid": "graph_a_1",
            "name": "WORKS_FOR",
            "fact": "Alice works for Acme Corp",
        }
    ])
    assert memory.count_edges("graph_a") == 1
    assert memory.list_edges("graph_a")[0]["fact"] == "Alice works for Acme Corp"

    shock_id = memory.add_market_shock("sim_1", "A sudden rate hike", round_num=2)
    pending = memory.get_pending_market_shocks("sim_1", round_num=2)
    assert [shock["id"] for shock in pending] == [shock_id]
    memory.consume_market_shock(shock_id)
    assert memory.get_pending_market_shocks("sim_1", round_num=5) == []
    assert memory.list_market_shocks("sim_1")[0]["consumed"] == 1

    memory.delete_agents_by_graph("graph_a")
    memory.delete_edges_by_graph("graph_a")
    assert memory.count_agents("graph_a") == 0
    assert memory.count_edges("graph_a") == 0
    assert memory.count_agents("graph_b") == 1


def test_memory_consolidation_replaces_raw_interactions(tmp_path):
    memory = LocalMemory(str(tmp_path / "memory.db"))
    for round_num in range(1, 6):
        memory.add_interaction(round_num, "0", "CREATE_POST", f"event {round_num}")

    summary = __import__("asyncio").run(
        memory.consolidate_memory(
            round_number=5,
            llm_client=FakeConsolidationLLM(),
            interval=5,
        )
    )

    assert summary.startswith("Agents debated")
    assert memory.get_interactions_for_block(1, 5) == []
    assert memory.list_summaries()[0]["summary_text"] == summary

    memory.add_interaction(6, "0", "CREATE_POST", "another event")
    assert len(memory.get_interactions_for_block(6, 6)) == 1
    second = __import__("asyncio").run(
        memory.consolidate_memory(
            round_number=10,
            llm_client=FakeConsolidationLLM(),
            interval=5,
        )
    )
    assert second is not None
    # The second block owns rounds 6-10 and is cleared only after its summary.
    assert memory.get_interactions_for_block(6, 10) == []


def test_entity_extractor_deduplicates_and_links_entities(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RPM", 6000)

    class FakeExtractorLLM:
        def chat_json(self, messages=None, **kwargs):
            return {
                "entities": [
                    {
                        "name": "Alice",
                        "type": "Person",
                        "summary": "Alice leads the project.",
                        "attributes": {"role": "lead"},
                    },
                    {
                        "name": "Acme Corp",
                        "type": "Organization",
                        "summary": "Acme builds software.",
                        "attributes": {},
                    },
                    {
                        "name": "alice",
                        "type": "Person",
                        "summary": "Alice is based in Lahore.",
                        "attributes": {"city": "Lahore"},
                    },
                ],
                "relationships": [
                    {
                        "source": "Alice",
                        "target": "Acme Corp",
                        "relation": "WORKS_FOR",
                        "fact": "Alice works for Acme Corp.",
                    }
                ],
            }

    extractor = EntityExtractor(llm_client=FakeExtractorLLM())
    entities, edges, total_chunks = extractor.extract_graph(
        text="Alice works for Acme Corp. " * 80,
        ontology={"entity_types": [{"name": "Person"}, {"name": "Organization"}]},
        graph_id="graph_test",
        chunk_size=200,
        chunk_overlap=20,
        max_chunks=3,
    )

    assert total_chunks >= 3
    assert len(entities) == 2
    alice = next(entity for entity in entities if entity["name"].lower() == "alice")
    assert alice["graph_id"] == "graph_test"
    assert alice["attributes"]["role"] == "lead"
    assert alice["attributes"]["city"] == "Lahore"
    assert len(edges) == 1
    assert edges[0]["source_node_uuid"] == alice["uuid"]
    assert alice["related_edges"][0]["direction"] == "outgoing"


def _prepare_simulation(tmp_path, monkeypatch, monkeypatch_runner=True):
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path / "simulations"))
    monkeypatch.setattr(Config, "DATABASE_PATH", str(tmp_path / "veyronix.db"))
    monkeypatch.setattr(Config, "LLM_RPM", 6000)
    monkeypatch.setattr(Config, "LLM_MAX_CONCURRENT", 3)
    monkeypatch.setattr(Config, "MEMORY_CONSOLIDATION_INTERVAL", 5)
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path / "simulations"))

    manager = SimulationManager()
    state = manager.create_simulation("project_1", "graph_1", True, True)
    sim_dir = tmp_path / "simulations" / state.simulation_id
    sim_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "simulation_requirement": "Test the market reaction to a new policy.",
        "time_config": {
            "minutes_per_round": 60,
            "total_simulation_hours": 4,
            "peak_hours": list(range(0, 24)),
            "off_peak_hours": [],
            "agents_per_hour_max": 20,
        },
        "event_config": {
            "hot_topics": ["policy"],
            "narrative_direction": "Mixed reactions",
        },
        "agent_configs": [
            {
                "agent_id": 0,
                "activity_level": 1.0,
                "active_hours": list(range(0, 24)),
                "influence_weight": 1.0,
                "stance": "supportive",
                "entity_type": "Person",
            },
            {
                "agent_id": 1,
                "activity_level": 1.0,
                "active_hours": list(range(0, 24)),
                "influence_weight": 1.0,
                "stance": "opposed",
                "entity_type": "Organization",
            },
        ],
    }
    (sim_dir / "simulation_config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    (sim_dir / "reddit_profiles.json").write_text(
        json.dumps(
            [
                {
                    "user_id": 0,
                    "name": "Alice",
                    "username": "alice",
                    "bio": "Project lead",
                    "persona": "Alice leads the project and supports the policy.",
                },
                {
                    "user_id": 1,
                    "name": "Acme Corp",
                    "username": "acme",
                    "bio": "Software company",
                    "persona": "Acme Corp opposes the new policy.",
                },
            ]
        ),
        encoding="utf-8",
    )
    state.status = SimulationStatus.READY
    manager._save_simulation_state(state)
    return manager, state


def test_simulation_runner_end_to_end_with_market_shock_and_interviews(
    tmp_path, monkeypatch
):
    manager, state = _prepare_simulation(tmp_path, monkeypatch)
    memory = LocalMemory(Config.DATABASE_PATH)
    memory.add_market_shock(state.simulation_id, "Policy passed unexpectedly", round_num=1)

    fake_llm_holder = {}

    class FakeLLMClient(FakeChatLLM):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            fake_llm_holder["instance"] = self
            fake_llm_holder["prompts"] = self.prompts

    monkeypatch.setattr(runner_module, "LLMClient", FakeLLMClient)

    run_state = SimulationRunner.start_simulation(
        simulation_id=state.simulation_id,
        platform="parallel",
        max_rounds=2,
    )
    assert run_state.runner_status in {RunnerStatus.STARTING, RunnerStatus.RUNNING}

    deadline = time.time() + 30
    while time.time() < deadline:
        current = SimulationRunner.get_run_state(state.simulation_id)
        if current and current.runner_status in {
            RunnerStatus.COMPLETED,
            RunnerStatus.STOPPED,
            RunnerStatus.FAILED,
        }:
            break
        time.sleep(0.05)

    current = SimulationRunner.get_run_state(state.simulation_id)
    assert current is not None
    assert current.runner_status == RunnerStatus.COMPLETED, current.error
    assert current.current_round == 2
    assert current.twitter_actions_count + current.reddit_actions_count >= 2
    assert any(
        "MARKET SHOCK" in prompt and "Policy passed unexpectedly" in prompt
        for prompt in fake_llm_holder["prompts"]
    )

    shocks = memory.list_market_shocks(state.simulation_id)
    assert shocks[0]["consumed"] == 1

    actions = SimulationRunner.get_actions(state.simulation_id, limit=100)
    assert len(actions) == current.twitter_actions_count + current.reddit_actions_count
    assert SimulationRunner.get_timeline(state.simulation_id)
    assert SimulationRunner.get_agent_stats(state.simulation_id)
    assert SimulationRunner.check_env_alive(state.simulation_id)

    interview = SimulationRunner.interview_agent(
        simulation_id=state.simulation_id,
        agent_id=0,
        prompt="What do you think about the policy?",
        platform="reddit",
    )
    assert interview["success"] is True
    history = SimulationRunner.get_interview_history(state.simulation_id)
    assert history and history[0]["platform"] == "reddit"

    cleanup = SimulationRunner.cleanup_simulation_logs(state.simulation_id)
    assert cleanup["success"] is True

    reloaded = SimulationManager()._load_simulation_state(state.simulation_id)
    assert reloaded.status == SimulationStatus.COMPLETED


def test_graph_data_and_market_shock_endpoints(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setattr(Config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(Config, "API_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path / "simulations"))

    memory = LocalMemory(db_path)
    memory.save_agents([
        {
            "id": "node_1",
            "persona": {
                "uuid": "node_1",
                "name": "Alice",
                "type": "Person",
                "labels": ["Person"],
                "summary": "A founder.",
                "attributes": {"role": "CEO"},
                "graph_id": "graph_api",
            },
            "state": "created",
        }
    ])
    memory.save_edges([
        {
            "uuid": "edge_1",
            "graph_id": "graph_api",
            "source_node_uuid": "node_1",
            "target_node_uuid": "node_1",
            "name": "SELF",
            "fact": "Alice relies on Alice.",
        }
    ])

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    headers = {"Authorization": "Bearer test-token"}

    graph_response = client.get("/api/graph/data/graph_api", headers=headers)
    assert graph_response.status_code == 200
    graph = graph_response.json["data"]
    assert graph["node_count"] == 1
    assert graph["edge_count"] == 1
    assert graph["nodes"][0]["labels"] == ["Person"]
    assert graph["edges"][0]["source_node_uuid"] == "node_1"

    manager = SimulationManager()
    state = manager.create_simulation("project_api", "graph_api")

    shock_response = client.post(
        f"/api/simulation/{state.simulation_id}/market-shock",
        json={"event": "Unexpected regulation", "round_num": 2},
        headers=headers,
    )
    assert shock_response.status_code == 200
    assert shock_response.json["data"]["shock_id"] > 0

    list_response = client.get(
        f"/api/simulation/{state.simulation_id}/market-shocks",
        headers=headers,
    )
    assert list_response.status_code == 200
    assert list_response.json["data"]["count"] == 1

    missing_event = client.post(
        f"/api/simulation/{state.simulation_id}/market-shock",
        json={},
        headers=headers,
    )
    assert missing_event.status_code == 400

    unauthorized = client.get("/api/graph/data/graph_api")
    assert unauthorized.status_code == 401
