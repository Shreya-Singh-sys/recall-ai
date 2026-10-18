import re
import os
from dotenv import load_dotenv
from groq import Groq
from datetime import datetime, timezone
from fastapi.responses import HTMLResponse
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from falkordb import FalkorDB
from pathlib import Path
from fastapi.responses import FileResponse

db = FalkorDB(host="localhost", port=6379)

def get_tenant_graph(tenant_id: str):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", tenant_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid tenant_id"
        )

    return db.select_graph(f"tenant_{tenant_id}")

load_dotenv()
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = FastAPI(
    title="RecallAI",
    description="Shared memory and coordination for AI agents",
    version="2.0"
)

STOP_WORDS = {
    "the", "and", "for", "are", "you", "with",
    "this", "that", "from", "use", "using",
    "task", "agent", "please", "make"
}



def retrieve_relevant_memories(task, agent_id, graph, limit=5):
    result = graph.query(
        """
        MATCH (m:Memory)
        WHERE (
            m.visibility = 'shared'
            OR (
                m.visibility = 'private'
                AND m.agent_id = $agent_id
            )
        )
        AND coalesce(m.superseded, false) = false
        RETURN id(m), m.content, m.created_at, coalesce(m.type, 'semantic')
        """,
        params={"agent_id": agent_id}
    )

    task_words = {
        word for word in re.findall(r"\w+", task.lower())
        if len(word) > 2 and word not in STOP_WORDS
    }

    memories = []

    for row in result.result_set:
        content = row[1] or ""
        content_words = set(re.findall(r"\w+", content.lower()))

        memories.append({
            "memory_id": row[0],
            "content": content,
            "created_at": row[2] or "",
            "type": row[3],
            "score": len(task_words & content_words)
        })

    memories.sort(
        key=lambda item: (item["score"], item["created_at"]),
        reverse=True
    )

    relevant = [m for m in memories if m["score"] > 0]

    if not relevant:
        relevant = memories

    return relevant[:limit]



class MemoryInput(BaseModel):
    tenant_id: str
    agent_id: str
    content: str
    visibility: str = "shared"
    memory_type: str = "semantic"


class MemoryUpdateInput(BaseModel):
    tenant_id: str
    agent_id: str
    old_content: str
    new_content: str



class CoordinationInput(BaseModel):
    tenant_id: str
    from_agent: str
    to_agent: str
    task: str
    decision: str


@app.get("/")
def home():
    return {
        "project": "RecallAI",
        "status": "running",
        "features": ["shared memory", "memory search", "coordination"]
    }


@app.post("/memories")
def save_memory(memory: MemoryInput):
    if memory.visibility not in ("shared", "private"):
        raise HTTPException(
            status_code=400,
            detail="visibility must be shared or private"
        )
    allowed_types = {"semantic", "episodic", "procedural"}

    if memory.memory_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="memory_type must be semantic, episodic, or procedural"
        )
    graph = get_tenant_graph(memory.tenant_id)
    created_at = datetime.now(timezone.utc).isoformat()

    result = graph.query(
        """
        MERGE (a:Agent {agent_id: $agent_id})
        CREATE (m:Memory {
            agent_id: $agent_id,
            content: $content,
            visibility: $visibility,
            created_at: $created_at,
            type: $memory_type,
            superseded: false
        })
        CREATE (a)-[ :WROTE]->(m)
        RETURN m.content
        """,
        params={
            "agent_id": memory.agent_id,
            "content": memory.content,
            "visibility": memory.visibility,
            "created_at": created_at,
            "memory_type": memory.memory_type
        }
    )

    return {
        "status": "saved",
        "tenant_id": memory.tenant_id,
        "agent_id": memory.agent_id,
        "visibility": memory.visibility,
        "content": result.result_set[0][0]
    }


@app.post("/memories/update")
def update_memory(memory: MemoryUpdateInput):
    if not memory.old_content.strip() or not memory.new_content.strip():
        raise HTTPException(
            status_code=400,
            detail="Old and new memory content cannot be empty"
        )
    graph = get_tenant_graph(memory.tenant_id)
    created_at = datetime.now(timezone.utc).isoformat()

    result = graph.query(
        """
        MATCH (old:Memory)
        WHERE old.agent_id = $agent_id
          AND old.content = $old_content
          AND coalesce(old.superseded, false) = false
        WITH old
        MERGE (a:Agent {agent_id: $agent_id})
        CREATE (new:Memory {
            agent_id: $agent_id,
            content: $new_content,
            visibility: old.visibility,
            created_at: $created_at,
            type:coalesce(old.type, 'semantic'),
            superseded: false
        })
        CREATE (new)-[:SUPERSEDES]->(old)
        CREATE (a)-[:WROTE]->(new)
        SET old.superseded = true
        RETURN new.content, old.content, old.visibility
        """,
        params={
            "agent_id": memory.agent_id,
            "old_content": memory.old_content,
            "new_content": memory.new_content,
            "created_at": created_at
        }
    )

    if not result.result_set:
        raise HTTPException(
            status_code=404,
            detail="Active memory not found for this agent"
        )

    return {
        "status": "updated",
        "tenant_id": memory.tenant_id,
        "agent_id": memory.agent_id,
        "new_content": result.result_set[0][0],
        "superseded_content": result.result_set[0][1],
        "visibility": result.result_set[0][2]
    }


@app.get("/memories/{tenant_id}/{agent_id}")
def get_memories(tenant_id: str, agent_id: str):
    graph = get_tenant_graph(tenant_id)
    result = graph.query(
        """
        MATCH (m:Memory)
        WHERE (
            m.visibility = 'shared'
            OR m.agent_id = $agent_id
        )
        AND coalesce(m.superseded, false) = false
        RETURN m.agent_id, m.content, m.visibility, m.created_at, coalesce(m.type, 'semantic') 
        ORDER BY m.created_at DESC
        """,
        params={"agent_id": agent_id}
    )

    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "memories": [
            {
                "owner": row[0],
                "content": row[1],
                "visibility": row[2],
                "created_at": row[3],
                "type": row[4]
            }
            for row in result.result_set
        ]
    }


@app.get("/search/{tenant_id}/{agent_id}")
def search_memories(tenant_id: str, agent_id: str, q: str):
    graph = get_tenant_graph(tenant_id)
    result = graph.query(
        """
        MATCH (m:Memory)
        WHERE (
            m.visibility = 'shared'
            OR m.agent_id = $agent_id
        )
        AND coalesce(m.superseded, false) = false
        AND toLower(m.content) CONTAINS toLower($q)
        RETURN m.agent_id, m.content, m.visibility, m.created_at, coalesce(m.type, 'semantic')
        ORDER BY m.created_at DESC
        """,
        params={"agent_id": agent_id, "q": q}
    )

    return {
        "tenant_id": tenant_id,
        "query": q,
        "results": [
            {
                "owner": row[0],
                "content": row[1],
                "visibility": row[2],
                "created_at": row[3],
                "type": row[4]
            }
            for row in result.result_set
        ]
    }


@app.post("/coordination")
def record_coordination(item: CoordinationInput):
    graph = get_tenant_graph(item.tenant_id)
    created_at = datetime.now(timezone.utc).isoformat()

    graph.query(
        """
        MERGE (a:Agent {agent_id: $from_agent})
        MERGE (b:Agent {agent_id: $to_agent})
        CREATE (c:Coordination {
            from_agent: $from_agent,
            to_agent: $to_agent,
            task: $task,
            decision: $decision,
            created_at: $created_at
        })
        CREATE (a)-[:COORDINATED]->(c)
        CREATE (c)-[:INVOLVES]->(b)
        """,
        params={
            "from_agent": item.from_agent,
            "to_agent": item.to_agent,
            "task": item.task,
            "decision": item.decision,
            "created_at": created_at
        }
    )

    return {
        "status": "recorded",
        "tenant_id": item.tenant_id,
        "from_agent": item.from_agent,
        "to_agent": item.to_agent,
        "task": item.task,
        "decision": item.decision
    }


@app.get("/coordination/{tenant_id}/{agent_id}")
def get_coordination(tenant_id: str, agent_id: str):
    graph = get_tenant_graph(tenant_id)
    result = graph.query(
        """
        MATCH (c:Coordination)
        WHERE c.from_agent = $agent_id OR c.to_agent = $agent_id
        RETURN c.from_agent, c.to_agent, c.task,
               c.decision, c.created_at
        """,
        params={"agent_id": agent_id}
    )

    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "coordination": [
            {
                "from_agent": row[0],
                "to_agent": row[1],
                "task": row[2],
                "decision": row[3],
                "created_at": row[4]
            }
            for row in result.result_set
        ]
    }


class AgentRunInput(BaseModel):
    tenant_id: str
    task: str
    from_agent: str = "agent-A"
    to_agent: str = "agent-B"


@app.post("/run-demo")
def run_agent_demo(request: AgentRunInput):
    if not request.task.strip():
        raise HTTPException(
            status_code=400,
            detail="Task cannot be empty"
        )
    graph = get_tenant_graph(request.tenant_id)

    # Step 1: Shared memory retrieve karo
    memories = retrieve_relevant_memories(request.task, agent_id=request.to_agent, graph=graph, limit=5)

    created_at = datetime.now(timezone.utc).isoformat()

    # Step 2: Graph mein agents aur task save karo
    task_result = graph.query(
        """
        MERGE (a:Agent {agent_id: $from_agent})
        MERGE (b:Agent {agent_id: $to_agent})
        CREATE (t:Task {
            description: $task,
            status: 'assigned',
            created_at: $created_at
        })
        CREATE (a)-[:CREATED]->(t)
        CREATE (t)-[:ASSIGNED_TO]->(b)
        RETURN t.description, t.status
        """,
        params={
            "from_agent": request.from_agent,
            "to_agent": request.to_agent,
            "task": request.task,
            "created_at": created_at
        }
    )

    return {
        "status": "success",
        "tenant_id": request.tenant_id,
        "task": task_result.result_set[0][0],
        "task_status": task_result.result_set[0][1],
        "created_by": request.from_agent,
        "assigned_to": request.to_agent,
        "shared_memories_found": len(memories),
        "context_for_agent": [m["content"] for m in memories],
        "message": "Task assigned and shared context retrieved."
    }

class ExecuteTaskInput(BaseModel):
    tenant_id: str
    agent_id: str = "agent-B"

def generate_agent_response(task_description, memories, agent_id):
    context = "\n".join(
        f"- {memory}" for memory in memories
    ) if memories else "No relevant shared memories found."

    completion = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an AI agent in RecallAI. "
                    "Complete the assigned task using the provided "
                    "shared memories when relevant. Do not invent "
                    "facts that are not supported by the context. "
                    "If information is missing, clearly say so. "
                    "Give a useful, direct result."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Agent ID: {agent_id}\n\n"
                    f"Task:\n{task_description}\n\n"
                    f"Relevant shared memories:\n{context}\n\n"
                    "Execute the task and provide the result."
                )
            }
        ],
        temperature=0.3,
        max_tokens=500
    )

    return completion.choices[0].message.content



@app.post("/execute-task")
def execute_task(request: ExecuteTaskInput):
    graph = get_tenant_graph(request.tenant_id)
    print("DEBUG tenant_id:", request.tenant_id)
    print("DEBUG graph object:", graph)
    # 1. Atomically claim one assigned task
    claim_result = graph.query(
        """
        MATCH (t:Task)-[:ASSIGNED_TO]->(a:Agent)
        WHERE a.agent_id = $agent_id
          AND t.status = 'assigned'
        WITH t, a
        ORDER BY t.created_at DESC
        LIMIT 1
        SET t.status = 'executing'
        RETURN t.description, t.created_at
        """,
        params={"agent_id": request.agent_id}
    )

    if not claim_result.result_set:
        raise HTTPException(
            status_code=404,
            detail="No assigned task found for this agent in this tenant"
        )

    task_description = claim_result.result_set[0][0]
    task_created_at = claim_result.result_set[0][1]

    try:
        memories = retrieve_relevant_memories(
            task_description,
            agent_id=request.agent_id,
            graph=graph,
            limit=5
        )
        print("DEBUG task:", task_description)
        print("DEBUG retrieved memory count:", len(memories))
        print("DEBUG retrieved memories:", memories)
        print("DEBUG task_created_at:", repr(task_created_at))

        result_text = generate_agent_response(
            task_description=task_description,
            memories=[m["content"] for m in memories],
            agent_id=request.agent_id
        )

        update_result = graph.query(
            """
            MATCH (t:Task)-[:ASSIGNED_TO]->(a:Agent)
            WHERE a.agent_id = $agent_id
              AND t.created_at = $task_created_at
              AND t.status = 'executing'
            WITH t, a
            SET t.status = 'completed',
                t.result = $result
            CREATE (m:Memory {
                agent_id: $agent_id,
                content: $result,
                visibility: 'shared',
                type: 'episodic',
                created_at: $created_at,
                superseded: false
            })
            CREATE (a)-[:COMPLETED]->(t)
            CREATE (a)-[:WROTE]->(m)
            CREATE (m)-[:RESULT_OF]->(t)
            RETURN t.status, t.result
            """,
            params={
                "agent_id": request.agent_id,
                "task_created_at": task_created_at,
                "result": result_text,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        )

        if not update_result.result_set:
            raise HTTPException(
                status_code=409,
                detail="Task status changed before completion"
            )
        memory_ids =[m["memory_id"] for m in memories]
        print("DEBUG memory_ids:", memory_ids)
        if memory_ids:
            used_result=graph.query(
                """
                MATCH (t:Task)
                WHERE t.created_at = $task_created_at
                WITH t
                MATCH (m:Memory)
                WHERE id(m) IN $memory_ids
                MERGE (t)-[r:USED]->(m)
                RETURN count(r)
                """,
                params={
                    "task_created_at": task_created_at,
                    "memory_ids": memory_ids
                }
            )
            print("DEBUG USED query result:", used_result.result_set)

            
        verify_result = graph.query("""
            MATCH ()-[r:USED]->()
            RETURN count(r)
                    """)
        print("DEBUG graph USED count:", verify_result.result_set)





        return {
            "status": "completed",
            "tenant_id": request.tenant_id,
            "agent_id": request.agent_id,
            "task": task_description,
            "result": result_text
        }

    except HTTPException:
        raise
    except Exception as e:
        # Release the task if execution fails, so it can be retried
        graph.query(
            """
            MATCH (t:Task)-[:ASSIGNED_TO]->(a:Agent)
            WHERE a.agent_id = $agent_id
              AND t.created_at = $task_created_at
              AND t.status = 'executing'
            SET t.status = 'assigned'
            """,
            params={
                "agent_id": request.agent_id,
                "task_created_at": task_created_at
            }
        )
        raise HTTPException(
            status_code=502,
            detail=f"AI task execution failed: {str(e)}"
        )


BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "static" / "index.html"


@app.get("/dashboard", response_class=FileResponse)
def dashboard():
    return FileResponse(HTML_FILE)


@app.get("/tasks/{tenant_id}")
def list_tasks(tenant_id: str):
    graph = get_tenant_graph(tenant_id)
    result = graph.query(
        """
        MATCH (t:Task)
        OPTIONAL MATCH (a:Agent)-[:CREATED]->(t)
        OPTIONAL MATCH (t)-[:ASSIGNED_TO]->(b:Agent)
        RETURN t.description, t.status, t.result,
               t.created_at, a.agent_id, b.agent_id
        ORDER BY t.created_at DESC
        LIMIT 50
        """
    )

    return {
        "tenant_id": tenant_id,
        "total": len(result.result_set),
        "tasks": [
            {
                "task": row[0],
                "status": row[1],
                "result": row[2],
                "created_at": row[3],
                "created_by": row[4],
                "assigned_to": row[5]
            }
            for row in result.result_set
        ]
    }
    
@app.get("/ai-test")
def ai_test():
    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are RecallAI, an assistant that helps "
                        "agents coordinate and use shared memory."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        "In two sentences, explain how RecallAI "
                        "can use FalkorDB to share memory between agents."
                    )
                }
            ],
            temperature=0.3,
            max_tokens=200
        )

        answer = completion.choices[0].message.content

        return {
            "status": "success",
            "model": completion.model,
            "answer": answer
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Groq request failed: {str(e)}"
        )


