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

## Basic ping and query tests

- Run the node/server:

`python -m src.cli.run_node`

- Send a ping to the node/server (replace `<address>` by the one displayed on the node side):

`python -m src.cli.send_message --mode ping -d <address>`

- Send a query to the node/server (replace `<address>` by the one displayed on the node side and `<prompt>` by the prompt you want to send):

`python -m src.cli.send_message --mode query --prompt <query> -d <address>`

## Basic dummy model with capabilities test

- Run the first node:

`python -m src.cli.run_node -p 8001 --model-name math-model --capabilities math`

- Run the second node (replace `<address>` by the one displayed on the first node side and `<peer_id>` by the ending part of the address of the first node after the last /):

`python -m src.cli.run_node -p 8000 --model-name general-model --capabilities general --known-peer-id <peed_id> --known-peer-addr <address> --known-peer-model math-model --known-peer-capabilities math`

- Send a query to the first node (replace `<address>` by the one displayed on the first node side):

`python -m src.cli.send_message --mode query -d <address> --prompt "do some math please"`

The first node should answer directly and locally.

- Send a query to the second node (replace `<address>` by the one displayed on the second node side):

`python -m src.cli.send_message --mode query -d <address> --prompt "do some math please"`

The second node should forward the request to the first node which should answer.
