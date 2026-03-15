# cs498-TSADAI

Trustworthy and Scalable Architectures for Decentralized AI Systems

# How to use

Install Python version >=3.13

Install UV: https://docs.astral.sh/uv/guides/projects/

`pip install uv`

Install dependencies through UV:

`uv sync`

Activate the virtual env with Windows:

`source .venv/Scripts/activate`

You're ready to use and dev on the project! :D

# Test project

Run the node/server:

`python -m src.cli.run_node`

Send a ping to the node/server (replace `<address>` by the one displayed on the node side):

`python -m src.cli.send_message --mode ping -d <address>`

Send a query to the node/server (replace `<address>` by the one displayed on the node side and `<prompt>` by the prompt you want to send):

`python -m src.cli.send_message --mode query --prompt <query> -d <address>`
