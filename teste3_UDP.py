import time
import argparse
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink

def configure_single_queue(net):
    """
    Configura uma fila única de 10 Mbps usando HTB com um buffer pequeno (pfifo limit 20).
    Isso força descarte de pacotes sob congestão, permitindo observar perda de UDP.
    """
    s1 = net.get('s1')
    interface = 's1-eth4'

    info(f"*** (FAIR TEST) Criando Fila Única de 10Mbps com buffer limitado em {interface}...\n")

    # Diminui o buffer de transmissão físico do driver (NIC buffer)
    s1.cmd(f'ifconfig {interface} txqueuelen 20')

    # Limpa qualquer qdisc anterior
    s1.cmd(f'tc qdisc del dev {interface} root')

    # Cria um root HTB com classe default 10
    s1.cmd(f'tc qdisc add dev {interface} root handle 1: htb default 10')

    # Classe pai com 10 Mbps
    s1.cmd(f'tc class add dev {interface} parent 1: classid 1:1 htb rate 10mbit')

    # Classe única com 10 Mbps
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:10 htb rate 10mbit ceil 10mbit')

    # Adiciona FIFO pequeno (fila de 20 pacotes)
    s1.cmd(f'tc qdisc add dev {interface} parent 1:10 handle 10: pfifo limit 20')

    info("*** Configuração de Fila Única concluída.\n")


def configure_qos_priority(net):
    """
    Configura duas classes:
        - Classe 1:10 → 6 Mbps garantidos, prioridade maior (H2)
        - Classe 1:20 → 4 Mbps garantidos, baixa prioridade (H1 e H3)
    Ambas com fila curta (pfifo limit 20) para observar perdas.
    """
    s1 = net.get('s1')
    interface = 's1-eth4'

    info(f"*** (QOS TEST) Aplicando QoS com prioridade para H2 em {interface}...\n")

    # Reduz o buffer no driver para aumentar descarte
    s1.cmd(f'ifconfig {interface} txqueuelen 20')

    # Remove qdisc anterior
    s1.cmd(f'tc qdisc del dev {interface} root')

    # Cria root HTB com classe default 20
    s1.cmd(f'tc qdisc add dev {interface} root handle 1: htb default 20')

    # Classe pai com 10 Mbps total
    s1.cmd(f'tc class add dev {interface} parent 1: classid 1:1 htb rate 10mbit')

    # Classe de alta prioridade (H2)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:10 htb rate 6mbit ceil 10mbit prio 1')
    s1.cmd(f'tc qdisc add dev {interface} parent 1:10 handle 10: pfifo limit 20')

    # Classe de baixa prioridade (H1 e H3)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:20 htb rate 4mbit ceil 10mbit prio 2')
    s1.cmd(f'tc qdisc add dev {interface} parent 1:20 handle 20: pfifo limit 20')

    # Filtros baseados no IP de origem para separar tráfego
    info("*** Aplicando filtros de IP de origem para cada classe...\n")
    
    # H2 → classe prioritária
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 1 u32 match ip src 10.0.0.2 flowid 1:10')

    # H1 → classe de menor prioridade
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 2 u32 match ip src 10.0.0.1 flowid 1:20')

    # H3 → classe de menor prioridade
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 2 u32 match ip src 10.0.0.3 flowid 1:20')

    info("*** Configuração de QoS com prioridade concluída.\n")


def run_testUDP(test_type):
    """
    Executa o cenário completo:
    - Cria topologia
    - Conecta ao controlador remoto Ryu
    - Aplica configuração de filas (fair ou qos)
    - Executa 3 fluxos UDP com competição escalonada (10s de diferença)
    - Salva os resultados de iperf em /tmp
    """
    # Conecta ao controlador Ryu externo
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653)

    # Cria Mininet com OVS e controle remoto
    net = Mininet(controller=c0, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net.addController(c0)

    info('*** Adicionando hosts e switches\n')

    # Hosts com IP fixo
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')

    # Switches OpenFlow 1.3
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    info('*** Criando links\n')

    # Liga hosts ao switch s1
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s1)

    # Link de gargalo entre s1 e s2 (porta 4 de s1)
    net.addLink(s1, s2, port1=4, port2=1)

    # Saída final para h4
    net.addLink(s2, h4)

    info('*** Iniciando a rede\n')
    net.build()
    net.start()

    info('*** Aguardando switches se conectarem ao controlador...\n')
    time.sleep(5)

    # Escolhe modo FAIR ou QoS
    if test_type == 'qos':
        configure_qos_priority(net)
    else:
        configure_single_queue(net)

    info('*** Preparando o teste...\n')

    # Cria servidores UDP no h4 com portas separadas
    h4.cmd('iperf -s -u -p 5001 -i 1 > /tmp/iperf_h1_server.log &')
    h4.cmd('iperf -s -u -p 5002 -i 1 > /tmp/iperf_h2_server.log &')
    h4.cmd('iperf -s -u -p 5003 -i 1 > /tmp/iperf_h3_server.log &')

    info(f'*** INICIANDO TESTE UDP (MODO: {test_type.upper()}) ***\n')

    # Cada host envia 15 Mbps mesmo com link de 10 Mbps → força perda
    banda_envio = '15m'

    # Fluxo inicial somente H1
    info("--> [Tempo 0s] Iniciando fluxo H1 -> H4 (1 competidor)\n")
    h1.cmd(f'iperf -c 10.0.0.4 -p 5001 -u -b {banda_envio} -t 50 &')

    # Entra H2 10 segundos depois
    time.sleep(10)
    info("--> [Tempo 10s] Fluxo H2 ENTRA (2 competidores)\n")
    h2.cmd(f'iperf -c 10.0.0.4 -p 5002 -u -b {banda_envio} -t 30 &')

    # Entra H3 depois de mais 10 segundos
    time.sleep(10)
    info("--> [Tempo 20s] Fluxo H3 ENTRA (3 competidores)\n")
    h3.cmd(f'iperf -c 10.0.0.4 -p 5003 -u -b {banda_envio} -t 20 &')

    info("--> Testes em andamento... Aguardando 35 segundos para a conclusão final.\n")
    time.sleep(35)

    # Exibe resultados
    info('*** TESTE CONCLUÍDO ***\n\n')

    print('--- Resultados do Fluxo H1 -> H4 (Baixa Prioridade / Competidor) ---')
    print(h4.cmd('cat /tmp/iperf_h1_server.log'))

    print('--- Resultados do Fluxo H2 -> H4 (Alta Prioridade / Competidor) ---')
    print(h4.cmd('cat /tmp/iperf_h2_server.log'))

    print('--- Resultados do Fluxo H3 -> H4 (Baixa Prioridade / Competidor) ---')
    print(h4.cmd('cat /tmp/iperf_h3_server.log'))

    # Limpa processos e arquivos temporários
    h4.cmd('killall iperf')
    h4.cmd('rm /tmp/iperf_*.log')

    info('*** Parando a rede\n')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')

    parser = argparse.ArgumentParser(description='Executa testes de competição UDP com e sem QoS.')
    parser.add_argument('--test', type=str, default='fair', choices=['fair', 'qos'],
                        help="Tipo de teste: 'fair' para divisão justa, 'qos' para prioridade.")
    
    args = parser.parse_args()
    
    run_testUDP(args.test)
