CONTAINER:=fintech-to-ynab
REGISTRY:=192.168.2.111:50000
ENDPOINT:=$(REGISTRY)/ynab/$(CONTAINER)

build:
	docker build -t $(CONTAINER) .

run:
	{ \
		set -e; \
		if [ ! "$$(docker ps -q -f name=$(CONTAINER))" ]; then \
			if [ "$$(docker ps -aq -f status=exited -f name=$(CONTAINER))" ]; then docker rm $(CONTAINER); fi;  \
		docker run -d -p 5000:5000 --env-file .env --name $(CONTAINER) $(ENDPOINT); \
		fi;\
	}

run-remove:
	{ \
		set -e; \
		if [ ! "$$(docker ps -q -f name=$(CONTAINER))" ]; then \
			if [ "$$(docker ps -aq -f status=exited -f name=$(CONTAINER))" ]; then docker rm $(CONTAINER); fi;  \
		docker run -it -p 5000:5000 --env-file .env --rm --name $(CONTAINER) $(ENDPOINT); \
		fi;\
	}

push: build
	docker tag $(CONTAINER) $(REGISTRY)/ynab/$(CONTAINER)
	docker push $(ENDPOINT)

rm:
	docker stop fintech-to-ynab
	docker rm fintech-to-ynab


start:
	docker start $(CONTAINER)
