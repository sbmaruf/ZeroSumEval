import argparse
import logging

from collections import defaultdict

from zero_sum_eval.calculate_elo import calculate_elos
from zero_sum_eval.managers.game_pool_manager import GamePoolManager
from zero_sum_eval.registry import GAME_REGISTRY
from zero_sum_eval.logging_utils import cleanup_logging, setup_logging
from zero_sum_eval.config_utils import load_yaml_with_env_vars
from zero_sum_eval.managers import GameManager

logger = logging.getLogger(__name__)

def setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-c", "--config", type=str, default=None, help="Optional path to a YAML config file. This overwrites other arguments if passed.")
    parser.add_argument("-g", "--game", type=str, default=None)
    parser.add_argument("-o", "--output_dir", type=str, default='./zse_outputs', help="Output directory for logs and results.")

    parser.add_argument("-p", "--players", type=str, nargs="+", help='List of models to evaluate and their roles. (LiteLLM model names, for example, "white=openai/gpt-4o" or "black=openrouter/meta-llama/llama-3.3-70b-instruct")')
    parser.add_argument("--game_kwargs", type=str, nargs="+", default="", help="Arguments to pass to the game constructor as a string. For example, 'rebuttal_rounds=2', 'topics=topics.txt' for the debate game.")
    parser.add_argument("--max_rounds", type=int, default=100, help="Maximum number of rounds to play.")
    parser.add_argument("--max_player_attempts", type=int, default=5, help="Maximum number of attempts to generate a valid player move.")
    # Pool mode arguments
    parser.add_argument("--pool", action="store_true", help="Run a pool of matches instead of a single game.")
    parser.add_argument("-m", "--models", type=str, nargs="+", help="List of models to evaluate. (Only for pool mode)")
    parser.add_argument("--max_matches", type=int, default=10, help="Maximum number of matches to play. (Only for pool mode)")

    # Calculate ELOs arguments
    parser.add_argument("--calculate_elos", action="store_true", help="Calculate ELOs for the models.")
    parser.add_argument("--bootstrap_rounds", type=int, default=100, help="Number of bootstrap rounds to calculate ELOs.")

    # TODO: Add Player arguments. It only works with the default players for now.
    return parser

def read_config(path):
    config = load_yaml_with_env_vars(path)
    return defaultdict(dict, config)

def config_from_args(args):
    if args.pool:
        config = {
            "game": {"name": args.game, "args": {"players": {}}},
            "manager": {
                "max_matches": args.max_matches,
                "max_rounds_per_match": args.max_rounds,
                "max_player_attempts": args.max_player_attempts,
                "output_dir": args.output_dir,
            },
            "llms": [
                {"name": model.split('/')[-1], "model": model} for model in args.models
            ]
        }
    else:
        config = {
            "game": {"name": args.game, "args": {"players": {}}},
            "manager": {
                "max_player_attempts": args.max_player_attempts,
                "max_rounds": args.max_rounds,
                "output_dir": args.output_dir,
            },
        }
        game_args = dict([arg.split("=") for arg in args.game_kwargs])
        role_model_pairs = [model.split("=") for model in args.players]
        for role, model in role_model_pairs:
            config["game"]["args"]["players"][role] = {
                "args": {
                    "id": f"{role}_{model.split('/')[-1]}",
                    "lm": {"model": model},
                }
            }
    return config

def run_single_game(config):
    game_name = config.get("game", {})["name"]
    game_args = config.get("game", {}).get("args", {})
    # Set up logging with 'game' prefix
    handlers = setup_logging('game', output_dir=config["manager"]["output_dir"])

    try:
        game = GAME_REGISTRY.build(game_name, **game_args)
        game_manager = GameManager(
            max_rounds=config["manager"]["max_rounds"], 
            max_player_attempts=config["manager"]["max_player_attempts"], 
            output_dir=config["manager"]["output_dir"]
        )
        logger.info("Starting a new game")
        final_state = game_manager.start(game)
        logger.info(
            f"\nGame over. Final state:\n{final_state['game_state'].display()}"
        )
    finally:
        # Clean up logging
        cleanup_logging(logger, handlers)

def run_pool_matches(config):
    # Set up logging with 'match_series' prefix
    handlers = setup_logging(config["game"]["name"], 'match_series')

    match_manager = GamePoolManager(
        **config["manager"],
        game=config["game"]["name"],
        game_args=config["game"]["args"],
        llm_configs=config["llms"],
    )
    logger.info("Starting a new match pool series!")
    wdl = match_manager.start()
    logger.info(f"Match pool series over. WDL: {wdl}")
    # Clean up logging
    cleanup_logging(logger, handlers)

def cli_run():
    parser = setup_parser()
    args = parser.parse_args()

    # If we are running a single game, we need to specify players
    if args.game and not args.pool and not args.calculate_elos and args.players is None:
        raise ValueError("Must specify players when running a single game.")
    
    # Determine if we are running any games
    is_running_games = args.pool or args.game

    # If we are not running any games and calculate_elos is True, we need to only calculate ELOs without running any games from the output directory
    if not is_running_games and args.calculate_elos and args.output_dir:
        elos = calculate_elos(
            logs_path=args.output_dir,
            bootstrap_rounds=args.bootstrap_rounds,
            max_player_attempts=args.max_player_attempts
        )
        print(elos)
        return

    if args.output_dir is None:
        args.output_dir = f"zse_outputs/{args.game}"
        if args.pool:
            args.output_dir += "_pool"

    if args.config:
        config = read_config(args.config)
    else:
        config = config_from_args(args)
    logger.info(f"Running {args.game} with config:\n{config}")

    # Run matches if pool mode is enabled
    if args.pool:
        run_pool_matches(config)

    # Calculate ELO ratings if requested
    if args.calculate_elos:
        elos = calculate_elos(
            logs_path=args.output_dir,
            bootstrap_rounds=args.bootstrap_rounds,
            max_player_attempts=args.max_player_attempts
        )
        print(elos)
    # Run single game if no other modes specified
    elif not args.pool and not args.calculate_elos:
        run_single_game(config)
    

if __name__ == "__main__":
    cli_run()
