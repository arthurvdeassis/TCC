import time
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink

# -------------------------------------------------------------------
# Função que aplica QoS usando HTB + PFIFO com duas classes (alta e baixa)
# -------------------------------------------------------------------
def configure_qos_udp_loss(net):
    # Obtém o switch S1
    s1 = net.get('s1')

    # Interface entre S1 e S2 onde será aplicado o QoS
    interface = 's1-eth4'  

    info(f"*** Aplicando QoS via 'tc' com buffers limitados na interface {interface}...\n")
    
    # Reduz o tamanho da fila TX (buffer NIC) para aumentar perda quando congestiona
    s1.cmd(f'ifconfig {interface} txqueuelen 10')

    # Remove qualquer regra anterior
    s1.cmd(f'tc qdisc del dev {interface} root')

    # Cria disciplina raiz HTB (hierarchical token bucket)
    s1.cmd(f'tc qdisc add dev {interface} root handle 1: htb default 20')

    # Classe pai: capacidade total de 10 Mbps
    s1.cmd(f'tc class add dev {interface} parent 1: classid 1:1 htb rate 10mbit')
    
    # Classe **alta prioridade** — reservada 8 Mbps, mas pode usar até 10 Mbps
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:10 htb rate 8mbit ceil 10mbit prio 1')

    # Classe **baixa prioridade** — reservada 2 Mbps
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:20 htb rate 2mbit ceil 10mbit prio 2')
    
    # Fila de alta prioridade: pfifo com limite de 10 pacotes
    s1.cmd(f'tc qdisc add dev {interface} parent 1:10 handle 10: pfifo limit 10')

    # Fila de baixa prioridade: pfifo com limite de 10 pacotes
    s1.cmd(f'tc qdisc add dev {interface} parent 1:20 handle 20: pfifo limit 10')

    info("*** Aplicando filtros de IP de origem para cada classe...\n")

    # Tráfego de h1 (10.0.0.1) vai para a classe 1:10 (alta prioridade)
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 1 u32 match ip src 10.0.0.1 flowid 1:10')

    # Tráfego de h3 (10.0.0.3) vai para classe 1:20 (baixa prioridade)
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 2 u32 match ip src 10.0.0.3 flowid 1:20')
    
    info("*** Configuração de QoS para o cenário de apresentação concluída.\n")


# -------------------------------------------------------------------
# Função que monta a topologia e roda o teste UDP
# -------------------------------------------------------------------
def run_testUDP():

    # Controller remoto Ryu
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653)

    # Cria rede com switches OVS e suporte a TCLink (para controle de link)
    net = Mininet(controller=c0, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net.addController(c0)

    info('*** Adicionando hosts e switches\n')

    # Adiciona hosts com IPs fixos
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')

    # Switches com OpenFlow 1.3
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    info('*** Criando links\n')

    # Hosts conectados ao s1
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s1)

    # Conexão entre s1 e s2 — porta 4 de s1 é utilizada pelo QoS
    net.addLink(s1, s2, port1=4, port2=1)

    # Host final conectado ao s2
    net.addLink(s2, h4)

    info('*** Iniciando a rede\n')
    net.build()
    net.start()
    
    info('*** Aguardando switches se conectarem ao controlador...\n')
    time.sleep(5)  # tempo para Ryu instalar regras automáticas
    
    # Aplica o QoS configurado acima
    configure_qos_udp_loss(net)
    
    info('*** Preparando o teste...\n')

    # Servidores UDP rodando no h4 (um para H1 e outro para H3)
    h4.cmd('iperf -s -u -p 5001 -i 1 > /tmp/iperf_h1_server_udp.log &')
    h4.cmd('iperf -s -u -p 5002 -i 1 > /tmp/iperf_h3_server_udp.log &')
    
    # Inicia medição de latência
    info("--> Iniciando medição de latência (ping) de H1 -> H4\n")
    h1.cmd('ping 10.0.0.4 > /tmp/ping_results.log &')
    
    info('*** INICIANDO TESTE IPERF COM UDP ***\n')

    # Primeiro fluxo: baixa prioridade (H3)
    info("--> [Tempo 0s] Iniciando fluxo de baixa prioridade (H3 -> H4) com 15Mbps\n")
    h3.cmd('iperf -c 10.0.0.4 -p 5002 -u -b 15m -t 40 &')

    # Espera 10s e inicia fluxo de alta prioridade
    time.sleep(10)

    info("--> [Tempo 10s] Iniciando fluxo de alta prioridade (H1 -> H4) com 15Mbps\n")
    h1.cmd('iperf -c 10.0.0.4 -p 5001 -u -b 15m -t 20 &')
    
    info("--> Testes em andamento... Aguardando 35 segundos para a conclusão.\n")
    time.sleep(35)

    info('*** TESTE CONCLUÍDO ***\n\n')
    
    # Para o ping
    h1.cmd('killall ping')
    
    # Mostra logs capturados
    print('--- Resultados do Fluxo de BAIXA PRIORIDADE (H3 -> H4) [UDP] ---')
    print(h4.cmd('cat /tmp/iperf_h3_server_udp.log'))

    print('--- Resultados do Fluxo de ALTA PRIORIDADE (H1 -> H4) [UDP] ---')
    print(h4.cmd('cat /tmp/iperf_h1_server_udp.log'))

    print('\n--- Resultados de Latência (PING H1 -> H4) ---')
    print(h1.cmd('cat /tmp/ping_results.log'))

    # Limpa processos e arquivos
    h4.cmd('killall iperf')
    h4.cmd('rm /tmp/iperf_*.log')
    h1.cmd('rm /tmp/ping_results.log')

    info('*** Parando a rede\n')
    net.stop()


# -------------------------------------------------------------------
# Execução do script
# -------------------------------------------------------------------
if __name__ == '__main__':
    setLogLevel('info')
    run_testUDP()
