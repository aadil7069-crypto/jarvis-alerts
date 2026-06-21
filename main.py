import asyncio
import logging

from core.config import load_config
from core.logger import setup_logger
from core.database import init_database
from core.message_bus import MessageBus
from core.circuit_breaker import CircuitBreaker
from core.validator import validate_startup

from agents.orchestrator import Orchestrator
from agents.research_agent import ResearchAgent
from agents.whale_agent import WhaleAgent
from agents.smart_money_agent import SmartMoneyAgent
from agents.elite_trader_agent import EliteTraderAgent
from agents.sentiment_agent import SentimentAgent
from agents.risk_agent import RiskAgent
from agents.vetting_agent import VettingAgent
from agents.strategy_agent import StrategyAgent
from agents.execution_agent import ExecutionAgent
from agents.portfolio_agent import PortfolioAgent
from agents.learning_agent import LearningAgent
from agents.reporting_agent import ReportingAgent
from agents.prediction_market_agent import PredictionMarketAgent
from agents.regime_agent import RegimeAgent


async def _run_agent_safely(agent, logger: logging.Logger) -> None:
    """
    Wrap an agent so its crash is isolated — one failing agent does not
    bring down the rest of the system. Restarts the agent after 30s.
    """
    while agent.running is not False:
        try:
            await agent.start()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"Agent '{agent.name}' crashed: {e} — restarting in 30s",
                exc_info=True,
            )
            agent.running = False          # reset so start() re-initialises
            await asyncio.sleep(30)
            agent.running = True


async def main() -> None:
    config = load_config()
    setup_logger(config)
    logger = logging.getLogger("jarvis.main")

    logger.info("=" * 60)
    logger.info("  JARVIS 360 — Starting up")
    logger.info(f"  Mode    : {config['system']['mode'].upper()}")
    logger.info(f"  Version : {config['system']['version']}")
    logger.info("=" * 60)

    # Fix #9: validate secrets before starting agents
    validate_startup(config)

    db_factory = init_database(config)
    bus = MessageBus()
    cb = CircuitBreaker(config)

    agents = [
        Orchestrator("orchestrator",         bus, db_factory, cb, config),
        ResearchAgent("research",            bus, db_factory, config),
        WhaleAgent("whale",                  bus, db_factory, config),
        SmartMoneyAgent("smart_money",       bus, db_factory, config),
        EliteTraderAgent("elite_trader",     bus, db_factory, config),
        SentimentAgent("sentiment",          bus, db_factory, config),
        RiskAgent("risk",                    bus, db_factory, cb, config),
        VettingAgent("vetting",              bus, db_factory, config),
        StrategyAgent("strategy",            bus, db_factory, config),
        ExecutionAgent("execution",          bus, db_factory, cb, config),
        PortfolioAgent("portfolio",          bus, db_factory, config),
        LearningAgent("learning",            bus, db_factory, config),
        ReportingAgent("reporting",          bus, db_factory, config),
        PredictionMarketAgent("prediction_market", bus, db_factory, config),
        RegimeAgent("regime",                bus, db_factory, config),
    ]

    logger.info(f"Starting {len(agents)} agents with crash isolation...")

    try:
        # Fix #2: each agent runs in its own isolated task — one crash = one restart
        await asyncio.gather(
            bus.run_forever(),
            *[_run_agent_safely(agent, logger) for agent in agents],
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested — stopping all agents...")
        for agent in agents:
            await agent.stop()
        logger.info("Jarvis 360 shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
