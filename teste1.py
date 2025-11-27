import os
import time
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink

# Parâmetros globais de teste
TEST_DURATION = 15          # Tempo de cada execução do iperf
LINK_BANDWIDTH = 10         # Largura de banda dos links (Mbps)
IPERF_INTERVAL = 1          # Intervalo de atualização do iperf
MAX_Q_SIZE = 10             # Tamanho máximo da fila (em pacotes)

class SymmetricTCLink(TCLink):
    """
    Classe customizada para aplicar TBF simétrico (Token Bucket Filter)
    nos dois lados do link. Isso força que tanto envio quanto recebimento
    obedeçam ao limite de banda definido.
    """
    def config(self, **params):

        # Remove parâmetro HTB se existir (não queremos HTB)
        params.pop('use_htb', None)

        # Configura o link base normalmente
        super().config(**params)

        bw = params.get('bw', None)  # Banda especificada no link

        # Se houver banda configurada, aplica TBF nas interfaces
        if bw:
            for intf in [self.intf1, self.intf2]:
                # Remove qualquer qdisc existente
                self.cmd(f'tc qdisc del dev {intf} root 2>/dev/null')
                # Aplica um TBF simples
                self.cmd(
                    f'tc qdisc add dev {intf} root tbf rate {bw}mbit burst 1k latency 1ms'
                )
                info(f'*** Limitando {intf} a {bw} Mbps\n')


def run_tests():
    """Função principal que monta toda a topologia e executa os testes."""

    # Criação do controlador remoto (Ryu)
    c0 = RemoteController('c0', ip='127.0.0.1', port=6633)

    # Criação da rede com:
    # - controlador remoto
    # - switches OVS
    # - links simétricos
    # - geração automática de MACs
    net = Mininet(controller=c0, switch=OVSKernelSwitch,
                  link=SymmetricTCLink, autoSetMacs=True)

    net.addController(c0)

    info('*** Adicionando hosts e switches\n')
    # Hosts da topologia
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')

    # Switches centrais
    s1 = net.addSwitch('s1')
    s2 = net.addSwitch('s2')

    info(f'*** Criando links com BW={LINK_BANDWIDTH}Mbps e Qtd Fila={MAX_Q_SIZE}\n')

    # Configuração padrão de links usados
    link_opts = dict(bw=LINK_BANDWIDTH, max_queue_size=MAX_Q_SIZE)

    # Ligações dos hosts aos switches
    net.addLink(h1, s1, **link_opts)
    net.addLink(h2, s1, **link_opts)
    net.addLink(h3, s1, **link_opts)
    net.addLink(h4, s2, **link_opts)

    # Core da rede: S1 ↔ S2
    net.addLink(s1, s2, **link_opts)

    info('*** Iniciando a rede\n')
    net.build()
    net.start()

    # Aguarda o Ryu carregar regras iniciais
    info('*** Aguardando 15 segundos para o controlador Ryu conectar...\n')
    time.sleep(15)

    # TESTE 0 — baseline
    info('*** Medindo métricas de linha de base (ping)...\n')
    ping_result = h1.cmd(f'ping -c 10 -i 1 {h4.IP()}')
    info("\n--- SAÍDA DO PING (Baseline) ---\n")
    info(ping_result)
    info("*** Medição de Baseline Concluída.\n")

    # TESTE 1 — TCP sem competição
    info('--- Iniciando Teste TCP Sem Competição (H1 para H4) ---\n')
    h4.cmd('iperf -s -p 5001 &')  # servidor TCP
    time.sleep(1)

    result_no_comp_tcp = h1.cmd(
        f'iperf -c {h4.IP()} -p 5001 -t {TEST_DURATION} -i {IPERF_INTERVAL}'
    )

    os.system('killall iperf')  # garante que o iperf parou
    time.sleep(1)

    info("\n--- SAÍDA DO IPERF (TCP Sem Competição H1) ---\n")
    info(result_no_comp_tcp)
    info('--- Teste TCP Sem Competição Concluído ---\n')

    # TESTE 2 — TCP com competição
    info('--- Iniciando Teste TCP Com Competição (H1 e H3 para H4) ---\n')
    h4.cmd('iperf -s -p 5001 &')
    time.sleep(1)

    h3_output_file_tcp = "/tmp/h3_iperf_tcp_output.txt"
    h3.cmd(
        f'iperf -c {h4.IP()} -p 5001 -t {TEST_DURATION} '
        f'-i {IPERF_INTERVAL} > {h3_output_file_tcp} &'
    )

    result_comp_h1_tcp = h1.cmd(
        f'iperf -c {h4.IP()} -p 5001 -t {TEST_DURATION} -i {IPERF_INTERVAL}'
    )

    time.sleep(2)
    os.system('killall iperf')  # encerra ambos clientes
    time.sleep(1)

    # Busca o output do H3
    result_comp_h3_tcp = ""
    if os.path.exists(h3_output_file_tcp):
        with open(h3_output_file_tcp, 'r') as f:
            result_comp_h3_tcp = f.read()
        os.remove(h3_output_file_tcp)

    info("\n--- SAÍDA DO IPERF (TCP Competição H1) ---\n")
    info(result_comp_h1_tcp)
    info("\n--- SAÍDA DO IPERF (TCP Competição H3) ---\n")
    info(result_comp_h3_tcp)
    info('--- Teste TCP Com Competição Concluído ---\n')

    # TESTE 3 — UDP sem competição
    info('--- Iniciando Teste UDP Sem Competição (H1 para H4) ---\n')
    udp_rate_no_comp = f'{int(LINK_BANDWIDTH * 0.9)}M'
    h4.cmd('iperf -s -u -p 5002 &')
    time.sleep(1)

    result_no_comp_udp = h1.cmd(
        f'iperf -u -c {h4.IP()} -p 5002 -b {udp_rate_no_comp} '
        f'-t {TEST_DURATION} -i {IPERF_INTERVAL}'
    )

    os.system('killall iperf')
    time.sleep(1)

    info("\n--- SAÍDA DO IPERF (UDP Sem Competição H1) ---\n")
    info(result_no_comp_udp)
    info('--- Teste UDP Sem Competição Concluído ---\n')

    # TESTE 4 — UDP com competição
    info('--- Iniciando Teste UDP Com Competição (H1 e H3 para H4) ---\n')

    udp_rate_comp_h1 = f'{int(LINK_BANDWIDTH * 0.8)}M'
    udp_rate_comp_h3 = f'{int(LINK_BANDWIDTH * 0.8)}M'

    h4.cmd('iperf -s -u -p 5002 &')
    time.sleep(1)

    h3_output_file_udp = "/tmp/h3_iperf_udp_output.txt"

    h3.cmd(
        f'iperf -u -c {h4.IP()} -p 5002 -b {udp_rate_comp_h3} '
        f'-t {TEST_DURATION} -i {IPERF_INTERVAL} > {h3_output_file_udp} &'
    )

    result_comp_h1_udp = h1.cmd(
        f'iperf -u -c {h4.IP()} -p 5002 -b {udp_rate_comp_h1} '
        f'-t {TEST_DURATION} -i {IPERF_INTERVAL}'
    )

    time.sleep(2)
    os.system('killall iperf')
    time.sleep(1)

    result_comp_h3_udp = ""
    if os.path.exists(h3_output_file_udp):
        with open(h3_output_file_udp, 'r') as f:
            result_comp_h3_udp = f.read()
        os.remove(h3_output_file_udp)

    info("\n--- SAÍDA DO IPERF (UDP Competição H1) ---\n")
    info(result_comp_h1_udp)
    info("\n--- SAÍDA DO IPERF (UDP Competição H3) ---\n")
    info(result_comp_h3_udp)
    info('--- Teste UDP Com Competição Concluído ---\n')

    # Encerramento da rede
    info('*** Parando a rede\n')
    net.stop()

    info(f'*** Teste FINAL concluído!.\n')


if __name__ == '__main__':
    setLogLevel('info')
    run_tests()
