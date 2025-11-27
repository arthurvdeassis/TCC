import time
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink

def configure_policing_and_qos(net):
    """
    Configura no switch s1:
      1) Policiamento (ingress policing) no tráfego vindo de H1
      2) Priorização (HTB + classes) no tráfego de saída em s1-eth4
    """
    s1 = net.get('s1')
    
    # -----------------------------
    # 1) POLICIAMENTO (INGRESS TBF)
    # -----------------------------
    info("*** Configurando Policiamento de Ingresso para H1 em s1-eth1...\n")
    ingress_if = 's1-eth1'  # Porta onde o tráfego do H1 entra em s1
    ifb_if = 'ifb0'         # Interface virtual usada para redirecionar pacotes ingressos

    # Cria interface IFB para realizar shaping/policing em tráfego de entrada
    s1.cmd('modprobe ifb numifbs=1')
    s1.cmd(f'ip link set dev {ifb_if} up')

    # Qdisc de ingresso que captura pacotes antes de serem entregues ao kernel
    s1.cmd(f'tc qdisc add dev {ingress_if} handle ffff: ingress')

    # Redireciona o tráfego recebido em ingress_if para a interface IFB
    s1.cmd(f'tc filter add dev {ingress_if} parent ffff: protocol ip u32 match u32 0 0 action mirred egress redirect dev {ifb_if}')

    # Aplica TBF (Token Bucket Filter) para limitar origem H1 a 2Mbps
    s1.cmd(f'tc qdisc add dev {ifb_if} root tbf rate 2mbit burst 20k latency 50ms')

    # -----------------------------
    # 2) QOS NO EGRESSO (HTB + PRIORIDADES)
    # -----------------------------
    egress_if = 's1-eth4'
    info(f"*** Configurando Priorização de Egresso em {egress_if}...\n")
    
    # Remove qdisc existente
    s1.cmd(f'tc qdisc del dev {egress_if} root')

    # Cria root HTB com default para a classe 30 (menor prioridade)
    s1.cmd(f'tc qdisc add dev {egress_if} root handle 1: htb default 30')

    # Classe pai: reserva total de 10Mbps no link s1 -> s2
    s1.cmd(f'tc class add dev {egress_if} parent 1: classid 1:1 htb rate 10mbit')
    
    # Classe 10: alta prioridade (H1)
    # Largura mínima garantida: 2Mbps, pode chegar até 10Mbps
    s1.cmd(f'tc class add dev {egress_if} parent 1:1 classid 1:10 htb rate 2mbit ceil 10mbit prio 1')

    # Classe 20: prioridade intermediária (H2)
    s1.cmd(f'tc class add dev {egress_if} parent 1:1 classid 1:20 htb rate 6mbit ceil 10mbit prio 2')

    # Classe 30: baixa prioridade (H3)
    s1.cmd(f'tc class add dev {egress_if} parent 1:1 classid 1:30 htb rate 2mbit ceil 10mbit prio 3')

    # -----------------------------
    # 3) FILTROS (classificação por IP de origem)
    # -----------------------------
    info("*** Aplicando filtros de egresso...\n")

    # H1 → classe 10
    s1.cmd(f'tc filter add dev {egress_if} protocol ip parent 1:0 prio 1 u32 match ip src 10.0.0.1 flowid 1:10')

    # H2 → classe 20
    s1.cmd(f'tc filter add dev {egress_if} protocol ip parent 1:0 prio 2 u32 match ip src 10.0.0.2 flowid 1:20')

    # H3 → classe 30
    s1.cmd(f'tc filter add dev {egress_if} protocol ip parent 1:0 prio 3 u32 match ip src 10.0.0.3 flowid 1:30')
    
    info("*** Configuração completa de Policiamento e QoS concluída.\n")

def run_testTCP():
    """
    Cria a topologia, aplica policiamento + QoS e executa três fluxos TCP
    chegando em momentos diferentes para observar:
      -> competição
      -> fairness vs prioridade
      -> impacto do policing de ingresso
    """
    # Define o controlador remoto (RYU)
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653)

    # Cria a rede com switch OVS + links configuráveis
    net = Mininet(controller=c0, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net.addController(c0)

    # -----------------------------
    # 1) Hosts e switches
    # -----------------------------
    info('*** Adicionando hosts e switches\n')
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')

    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    # -----------------------------
    # 2) Criação dos links físicos
    # -----------------------------
    info('*** Criando links\n')

    # H1 → s1 (porta 1)
    net.addLink(h1, s1, port1=0, port2=1)

    # H2 → s1 (porta 2)
    net.addLink(h2, s1, port1=0, port2=2)

    # H3 → s1 (porta 3)
    net.addLink(h3, s1, port1=0, port2=3)

    # s1 → s2 (porta 4)
    net.addLink(s1, s2, port1=4, port2=1)

    # s2 → h4 (porta 2)
    net.addLink(s2, h4, port1=2, port2=0)

    # -----------------------------
    # 3) Inicialização da rede
    # -----------------------------
    info('*** Iniciando a rede\n')
    net.build()
    net.start()
    
    info('*** Aguardando switches se conectarem ao controlador...\n')
    time.sleep(5)
    
    # Aplica policiamento + QoS
    configure_policing_and_qos(net)

    # -----------------------------
    # 4) Início dos testes TCP
    # -----------------------------
    info('*** Preparando o teste automatizado com TCP...\n')

    # Servidores iperf (H4 recebe todos)
    h4.cmd('iperf -s -p 5001 -i 1 > /tmp/iperf_h1_server.log &')
    h4.cmd('iperf -s -p 5002 -i 1 > /tmp/iperf_h2_server.log &')
    h4.cmd('iperf -s -p 5003 -i 1 > /tmp/iperf_h3_server.log &')
    
    info('*** INICIANDO TESTE COM POLICIAMENTO E PRIORIZAÇÃO***\n')

    # -----------------------------
    # Fluxo 1: H1 (alta prioridade + policiado)
    # -----------------------------
    info("--> [Tempo 0s] Iniciando fluxo H1 -> H4 (policiado a 2Mbps, alta prioridade)\n")
    h1.cmd('iperf -c 10.0.0.4 -p 5001 -t 50 &')
    
    time.sleep(10)

    # -----------------------------
    # Fluxo 2: H2 (prioridade intermediária)
    # -----------------------------
    info("--> [Tempo 10s] Fluxo H2 ENTRA (média prioridade)\n")
    h2.cmd('iperf -c 10.0.0.4 -p 5002 -t 30 &')
    
    time.sleep(10)

    # -----------------------------
    # Fluxo 3: H3 (baixa prioridade)
    # -----------------------------
    info("--> [Tempo 20s] Fluxo H3 ENTRA (baixa prioridade)\n")
    h3.cmd('iperf -c 10.0.0.4 -p 5003 -t 20 &')
    
    info("--> Testes em andamento... Aguardando 35 segundos para a conclusão final.\n")
    time.sleep(35)
    

    # -----------------------------
    # 5) Exibição dos resultados
    # -----------------------------
    info('*** TESTE CONCLUÍDO ***\n\n')

    print('--- Resultados do Fluxo H1 -> H4 (Policiado / Alta Prioridade) [TCP] ---')
    print(h4.cmd('cat /tmp/iperf_h1_server.log'))

    print('--- Resultados do Fluxo H2 -> H4 (Média Prioridade) [TCP] ---')
    print(h4.cmd('cat /tmp/iperf_h2_server.log'))

    print('--- Resultados do Fluxo H3 -> H4 (Baixa Prioridade) [TCP] ---')
    print(h4.cmd('cat /tmp/iperf_h3_server.log'))

    # Limpeza
    h4.cmd('killall iperf')
    h4.cmd('rm /tmp/iperf_*.log')

    info('*** Parando a rede\n')
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run_testTCP()
