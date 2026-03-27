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

## Basic dummy model with capabilities and DHT test

- Run the first node:

`python -m src.cli.run_node -p 8000 --dht-mode server --model-name bootstrap --capabilities general --advertise-address-mode ipv6_loopback`

- Run the second node (replace `<address>` by the one displayed on the first node side):

`python -m src.cli.run_node -p 8001 --dht-mode server --model-name math-node --capabilities math --bootstrap <address> --advertise-address-mode ipv6_loopback`

- Run the third node (replace `<address>` by the one displayed on the first node side):

`python -m src.cli.run_node -p 8002 --dht-mode client --model-name general-node --capabilities general --bootstrap <address> --advertise-address-mode ipv6_loopback`
