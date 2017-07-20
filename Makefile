CONTAINER:=ofx-to-ynab

build:
	docker build -t $(CONTAINER) .

run:
	{ \
		set -e; \
		if [ ! "$$(docker ps -q -f name=$(CONTAINER))" ]; then \
			if [ "$$(docker ps -aq -f status=exited -f name=$(CONTAINER))" ]; then docker rm $(CONTAINER); fi;  \
		docker run -d --name $(CONTAINER) $(CONTAINER); \
		fi;\
	}

run-remove:
	{ \
		set -e; \
		if [ ! "$$(docker ps -q -f name=$(CONTAINER))" ]; then \
			if [ "$$(docker ps -aq -f status=exited -f name=$(CONTAINER))" ]; then docker rm $(CONTAINER); fi;  \
		docker run -it --rm --name $(CONTAINER) $(CONTAINER); \
		fi;\
	}


start:
	docker start $(CONTAINER)
