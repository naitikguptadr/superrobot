"""Sample CrewAI agent fixture."""

from crewai import Agent, Crew, Task


def run_agent(topic: str) -> dict[str, str]:
    researcher = Agent(role="Researcher", goal="Research topics", backstory="Expert")
    task = Task(description=f"Research {topic}", agent=researcher)
    crew = Crew(agents=[researcher], tasks=[task])
    result = crew.kickoff()
    return {"response": str(result)}
