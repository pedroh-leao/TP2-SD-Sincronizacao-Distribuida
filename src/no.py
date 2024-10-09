import socket
import time
import random
import json
import threading
from constants import *
import sys

maximo_inteiro = sys.maxsize

class No:
    def __init__(self, id_no, num_de_nos, host, porta_cliente, porta_no_atual, proximo_host, proxima_porta_no, proximo_host_backup, proxima_porta_no_backup) -> None:
        self.id_no = id_no
        self.num_de_nos = num_de_nos
        self.host = host
        self.porta_cliente = porta_cliente   # Porta com a qual o cliente vai se conectar
        self.porta_no_atual = porta_no_atual # Porta com a qual o no anterior vai se conectar
        self.proximo_host = proximo_host
        self.proxima_porta_no = proxima_porta_no
        self.proximo_host_backup = proximo_host_backup              # tolerancia a falha
        self.proxima_porta_no_backup = proxima_porta_no_backup      # tolerancia a falha

        self.timestamp_cliente = None
        self.token = [None] * num_de_nos  # Inicializa o vetor vazio

        self.clienteConectado = False # Flag para sabermos se tem um cliente coonectado a esse no
        self.noAnteriorConectado = False # Flag para sabermos se o no anterior esta conectado

        # Iniciando sockets para a comunicacao com o cliente, o no anterior e o proximo no
        self.cliente_socket     = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Escuta e fala
        self.no_anterior_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Somente escuta
        self.no_seguinte_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Somente fala
        self.no_anterior_socket_backup = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Somente escuta
        self.no_seguinte_socket_backup = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Somente fala

        self.esperar_resposta = threading.Event()
        self.mutex = threading.Lock()
        
        self.print_na_tela = ""

    # Roda em uma thread separada
    def escutar_cliente(self):
        # Fazendo o binding
        self.cliente_socket.bind((self.host, self.porta_cliente))
        self.cliente_socket.listen()

        conn, addr = self.cliente_socket.accept()
    
        with conn:
            with self.mutex:
                self.clienteConectado = True
                print(f"Nó {self.id_no} conectado com o cliente {addr}")

            while True:
                dados = conn.recv(BUFFER_SIZE)
                if not dados:
                    break
                mensagem = json.loads(dados.decode())
                timestamp = mensagem.get('timestamp')
                #print(f"Nó {self.id_no} recebeu request do cliente com timestamp {timestamp}")
                self.timestamp_cliente = timestamp

                self.esperar_resposta.wait() # Bloqueando a thread, esperando o no sair da regiao critica

                conn.sendall(json.dumps({"status": "commited"}).encode())                    

    # Inicia a conexao com o no seguinte do anel e o de backup
    def conectar_ao_no_seguinte(self):
        self.no_seguinte_socket.connect(
            (self.proximo_host, self.proxima_porta_no)
        )
        self.no_seguinte_socket_backup(
            (self.proximo_host_backup, self.proxima_porta_no_backup)
        )


    # Realiza o bind para permitir que o no anterior possa se conectar
    def bind_para_no_anterior(self):
        # Principal
        self.no_anterior_socket.bind((self.host, self.porta_no_atual))
        self.no_anterior_socket.listen()
        #Backup
        self.no_anterior_socket_backup.bind((self.host, self.porta_no_atual))
        self.no_anterior_socket_backup.listen()

    # Escreve no token o timestamp do cliente, caso tenha informacoes, ou nulo, caso contrario
    def escrever_no_token(self):
        self.token[self.id_no] = self.timestamp_cliente
        # Exibindo na tela caso o novo valor para exibir seja diferente do anterior
        exibir = f"Nó {self.id_no} - Estado atual do vetor de tokens: {self.token}"
        if exibir != self.print_na_tela: 
            #print(exibir)
            self.print_na_tela = exibir

    # Tenta enviar o token para o proximo no do anel, se nao conseguir, envia para o backup
    # * AQUI FICA A LOGICA DO 1.1 E DO 1.2, ELAS ESTAO JUNTAS
    def enviar_para_proximo(self, vetor):
        try:
            self.no_seguinte_socket.sendall(json.dumps(vetor).encode())

        except ConnectionAbortedError:
            self.no_seguinte_socket = self.no_seguinte_socket_backup
            # O no da frente nao tera mais dados uma vez que ele caiu
            vetor[(self.id_no + 1) % self.num_de_nos] = None
            print(f"Nó {(self.id_no + 1) % self.num_de_nos} está fora do ar")
            self.enviar_para_proximo(vetor)

    # Fica esperando o no anterior enviar o token e atualiza o local com os novos valores
    def esperar_token(self):       
        if not self.noAnteriorConectado:
            self.conn_anterior, _ = self.no_anterior_socket.accept()
            self.noAnteriorConectado = True

        # Tenta receber o token de B
        try:
            dados = self.conn_anterior.recv(BUFFER_SIZE)
            if dados:
                token = json.loads(dados.decode())
                self.token = token
                if hasattr(self, 'temporizador'):  # Se houver um temporizador, ele deve ser cancelado ao receber de B
                    self.temporizador.cancel()

        except OSError as e:
            print(f"Erro ao receber dados de {(self.id_no - 1) % self.num_de_nos}: {e}")

        # Verifica se A enviou um token pelo backup
        try:
            conn_anterior_backup, _ = self.no_anterior_socket_backup.accept()
            dados_backup = conn_anterior_backup.recv(BUFFER_SIZE)
            if dados_backup:
                token_backup = json.loads(dados_backup.decode())
                # Se o temporizador não foi cancelado, quer dizer que B falhou, então aceita o token de A
                if self.temporizador.is_alive():
                    print(f"Nó {self.id_no} recebeu token de  via backup")
                    self.token = token_backup
                    self.iniciar_temporizador()  # Inicia o temporizador para a próxima rodada
        except OSError as e:
            print(f"Erro ao receber dados de {(self.id_no - 2) % self.num_de_nos}: {e}")


    # Inicia um temporizador de 5 segundos para verificar se o anterior falhou
    def iniciar_temporizador(self):
        self.temporizador = threading.Timer(5.0, self.deteccao_de_falha)
        self.temporizador.start()

    def deteccao_de_falha(self):
        print(f"Falha detectada no nó {(self.id_no - 1) % self.num_de_nos}. Nó {self.id_no} agora está recebendo diretamente de {(self.id_no - 2) % self.num_de_nos}.")
        # Quando o temporizador expira, muda a comunicação para o backup
        self.no_anterior_socket = self.no_anterior_socket_backup



    # Verifica se pode ou nao acessar a regiao critica
    def verificar_regiao_critica(self):
        tokens_copia = [x if x is not None else maximo_inteiro for x in self.token]

        menor_timestamp = min(tokens_copia)

        return self.timestamp_cliente == menor_timestamp and menor_timestamp != maximo_inteiro


    # Entra na regiao critica e executa o que deve executar. No caso, um sleep
    def entrar_regiao_critica(self):
        print(f"Nó {self.id_no} entrando na seção crítica...")
        tempo = random.uniform(0.2, 1)
        time.sleep(tempo)
        print(f"Nó {self.id_no} saiu da seção crítica.")

    def sair_da_regiao_critica(self):
        self.token[self.id_no] = None  # Limpa a posicao do vetor apos sair da regiao critica
        self.timestamp_cliente = None  # Remove o timestamp do cliente

        with self.mutex:
            if self.clienteConectado == True:
                self.esperar_resposta.set()    # Desbloqueando a thread do cliente para enviar o commit 

    def executar_no(self):
        self.bind_para_no_anterior()
        time.sleep(2)
        self.conectar_ao_no_seguinte()

        if self.id_no == 0:
            self.escrever_no_token()
            self.enviar_para_proximo(self.token)

        while True:
            self.esperar_token() 

            if self.verificar_regiao_critica():
                self.entrar_regiao_critica()
                self.sair_da_regiao_critica()

            else:
                self.escrever_no_token()

            self.enviar_para_proximo(self.token)

            # Iniciar o temporizador quando o token for recebido via socket de backup
            if self.no_anterior_socket == self.no_anterior_socket_backup:
                self.iniciar_temporizador()



if __name__ == "__main__":
    import sys
    if len(sys.argv) != 8:
        print("Uso: python3 no.py <id_no> <num_de_nos> <host> <porta_cliente> <porta_no_atual> <proximo_host> <proximo_port>")
        sys.exit(1)

    id_no = int(sys.argv[1])
    num_de_nos = int(sys.argv[2])
    host = sys.argv[3]
    porta_cliente = int(sys.argv[4])  # Porta para clientes
    porta_no_atual = int(sys.argv[5])  # Porta para comunicação entre nos
    proximo_host = sys.argv[6]
    proximo_port = int(sys.argv[7])

    no = No(id_no, num_de_nos, host, porta_cliente, porta_no_atual, proximo_host, proximo_port)
    
    # Inicia a thread para escutar o cliente
    threading.Thread(target=no.escutar_cliente, daemon=True).start()
    
    # Executa o loop principal do no
    no.executar_no()
    