import os
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from litellm import completion
from typing import TypedDict, Annotated
import asyncio
from github import Github
import uuid

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
gh = Github(GITHUB_TOKEN) if GITHUB_TOKEN else None

class AgentState(TypedDict):
    repo_name: str
    idea: str
    pm_decision: str
    code: str
    review: str
    log_messages: list

def run_llm(system_prompt: str, user_prompt: str):
    response = completion(
        model="groq/llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        api_key=GROQ_API_KEY
    )
    return response.choices[0].message.content

def brainstormer_node(state: AgentState):
    repo = state["repo_name"]
    
    readme_content = ""
    if gh:
        try:
            gh_repo = gh.get_repo(repo)
            readme = gh_repo.get_readme()
            readme_content = readme.decoded_content.decode('utf-8')
        except Exception as e:
            readme_content = "No README found or inaccessible."

    system_prompt = "You are the Visionary Agent. Your job is to brainstorm one single, highly innovative and feasible feature for the given GitHub repository. Use the provided README context to make it relevant."
    user_prompt = f"Brainstorm a new feature for the repository: {repo}.\n\nREADME Context:\n{readme_content[:1500]}\n\nKeep it under 3 sentences."
    
    idea = run_llm(system_prompt, user_prompt)
    state["idea"] = idea
    state["log_messages"].append({
        "type": "ui_update",
        "agentStatus": {"Visionary": "active"}
    })
    state["log_messages"].append({
        "type": "ui_update",
        "pipeline": {"id": "#NEW", "title": idea[:30] + "...", "status": "brainstorming", "agent": "Visionary"}
    })
    state["log_messages"].append({"agent": "Visionary", "msg": f"Proposed Feature: {idea}", "color": "text-emerald-400"})
    state["log_messages"].append({"type": "ui_update", "agentStatus": {"Visionary": "idle"}})
    return state

def pm_node(state: AgentState):
    idea = state["idea"]
    system_prompt = "You are the Product Manager. Review the proposed feature. Decide if we should build it ('APPROVED') or not ('REJECTED'). Start your response with APPROVED or REJECTED, then give a 1 sentence reason."
    
    decision = run_llm(system_prompt, f"Review this idea: {idea}")
    state["pm_decision"] = decision
    
    state["log_messages"].append({"type": "ui_update", "agentStatus": {"Reviewer": "active"}})
    state["log_messages"].append({
        "type": "ui_update",
        "pipeline": {"id": "#NEW", "title": idea[:30] + "...", "status": "reviewing", "agent": "Reviewer"}
    })
    
    is_approved = decision.startswith("APPROVED")
    msg_color = "text-amber-400" if is_approved else "text-red-400"
    state["log_messages"].append({"agent": "Reviewer", "msg": f"Decision: {decision}", "color": msg_color})
    state["log_messages"].append({"type": "ui_update", "agentStatus": {"Reviewer": "idle"}})
    return state

def should_implement(state: AgentState):
    return "implementer" if state["pm_decision"].startswith("APPROVED") else END

def implementer_node(state: AgentState):
    idea = state["idea"]
    system_prompt = "You are the Implementer Agent. Write a tiny python script that implements the core logic of the idea. Keep it very short."
    
    code = run_llm(system_prompt, f"Write code for: {idea}")
    state["code"] = code
    state["log_messages"].append({"type": "ui_update", "agentStatus": {"Implementer": "active"}})
    state["log_messages"].append({
        "type": "ui_update",
        "pipeline": {"id": "#NEW", "title": idea[:30] + "...", "status": "implementing", "agent": "Implementer"}
    })
    state["log_messages"].append({"agent": "Implementer", "msg": f"Generated code implementation.", "color": "text-blue-400"})
    state["log_messages"].append({"type": "ui_update", "agentStatus": {"Implementer": "idle"}})
    return state

def maintainer_node(state: AgentState):
    code = state["code"]
    repo_name = state["repo_name"]
    system_prompt = "You are the Maintainer. Review the code. Say 'LGTM - Merging PR' if it looks okay, or point out a flaw."
    
    review = run_llm(system_prompt, f"Review this code:\n{code}")
    state["review"] = review
    state["log_messages"].append({"type": "ui_update", "agentStatus": {"Maintainer": "active"}})
    state["log_messages"].append({"agent": "Maintainer", "msg": f"Code Review: {review}", "color": "text-purple-400"})
    
    if "LGTM" in review or "Merging" in review:
        state["log_messages"].append({
            "type": "ui_update",
            "activity": {"title": "Merged new feature", "time": "Just now", "type": "merge"}
        })
        
        # GitHub PR Logic
        if gh:
            try:
                state["log_messages"].append({"agent": "System", "msg": "Pushing code to GitHub...", "color": "text-zinc-400"})
                gh_repo = gh.get_repo(repo_name)
                default_branch = gh_repo.default_branch
                sb = gh_repo.get_branch(default_branch)
                
                # Create branch
                branch_name = f"feature/auto-generated-{uuid.uuid4().hex[:8]}"
                gh_repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=sb.commit.sha)
                
                # Extract python code block if present
                code_to_commit = code
                if "```python" in code:
                    code_to_commit = code.split("```python")[1].split("```")[0].strip()
                elif "```" in code:
                    code_to_commit = code.split("```")[1].split("```")[0].strip()
                
                # Create file
                gh_repo.create_file(
                    path=f"ai_feature_{uuid.uuid4().hex[:4]}.py",
                    message="Auto-generated feature by AutoMaintainer",
                    content=code_to_commit,
                    branch=branch_name
                )
                
                # Create PR
                pr = gh_repo.create_pull(
                    title="[AI] New Feature Implementation",
                    body=f"This PR was automatically generated by the AI AutoMaintainer.\n\n### Brainstormed Idea:\n{state['idea']}",
                    head=branch_name,
                    base=default_branch
                )
                state["log_messages"].append({"agent": "System", "msg": f"Successfully created PR: {pr.html_url}", "color": "text-emerald-500"})
            except Exception as e:
                state["log_messages"].append({"agent": "System", "msg": f"Failed to create PR: {str(e)}", "color": "text-red-500"})

    state["log_messages"].append({"type": "ui_update", "agentStatus": {"Maintainer": "idle"}})
    return state


# Build the Graph
workflow = StateGraph(AgentState)

workflow.add_node("brainstormer", brainstormer_node)
workflow.add_node("pm", pm_node)
workflow.add_node("implementer", implementer_node)
workflow.add_node("maintainer", maintainer_node)

workflow.set_entry_point("brainstormer")
workflow.add_edge("brainstormer", "pm")
workflow.add_conditional_edges("pm", should_implement)
workflow.add_edge("implementer", "maintainer")
workflow.add_edge("maintainer", END)

app = workflow.compile()

async def run_agent_loop(repo_name: str, ws_manager):
    # Sanitize repo_name to handle full URLs (e.g., https://github.com/owner/repo -> owner/repo)
    if "github.com/" in repo_name:
        repo_name = repo_name.split("github.com/")[-1].strip("/")
    
    # This generator yields logs back to the websocket
    initial_state = {"repo_name": repo_name, "idea": "", "pm_decision": "", "code": "", "review": "", "log_messages": []}
    
    # We will run this in a thread to not block asyncio if needed, 
    # but litellm has async, though we used sync for simplicity. 
    # To keep it streaming, we'll invoke the graph and just send updates.
    
    await ws_manager.broadcast({"agent": "System", "msg": f"Starting loop for repo: {repo_name}...", "color": "text-zinc-500"})
    
    last_idx = 0
    # LangGraph streams node outputs
    for s in app.stream(initial_state):
        # Find the newly added log messages and broadcast them
        node_name = list(s.keys())[0]
        state = s[node_name]
        
        new_msgs = state["log_messages"][last_idx:]
        for msg in new_msgs:
            await ws_manager.broadcast(msg)
            await asyncio.sleep(0.5) # Add a small delay so UI looks like it's "thinking"
            
        last_idx = len(state["log_messages"])

    await ws_manager.broadcast({"agent": "System", "msg": "Agent loop complete.", "color": "text-zinc-500"})
