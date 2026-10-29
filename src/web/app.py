"""Assemble the Oxidus web server."""
from src.web.bootstrap import app
from src.web.hub import hub
from src.web.services import runtime, dialogue, knowledge, support
from src.web.blueprints.chat import register as register_chat
from src.web.blueprints.wiki import register as register_wiki
from src.web.blueprints.admin import register as register_admin
from src.web.blueprints.files import register as register_files
from src.web.blueprints.learn import register as register_learn
from src.web.games_page import games_bp
from src.web.agent_page import agent_bp
from src.web.console import console_bp
from src.web.blueprints.obsidian import register as register_obsidian
from src.web.blueprints.brain import register as register_brain
from src.web.blueprints.settings import register as register_settings

register_chat(app)
register_wiki(app)
register_admin(app)
register_files(app)
register_learn(app)
app.register_blueprint(games_bp)
app.register_blueprint(agent_bp)
app.register_blueprint(console_bp)
register_obsidian(app)
register_brain(app)
register_settings(app)
