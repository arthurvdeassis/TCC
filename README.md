# Gerenciamento de Filas e Policiamento de Ingresso em Redes SDN

![Badge Concluído](https://img.shields.io/badge/Status-Concluído-green)
![Badge UFMG](https://img.shields.io/badge/Instituição-UFMG-red)
![Badge Python](https://img.shields.io/badge/Linguagem-Python-blue)
![Badge Mininet](https://img.shields.io/badge/Ferramenta-Mininet-orange)

> **Trabalho de Conclusão de Curso (TCC)**  
>
> **Instituição:** Universidade Federal de Minas Gerais (UFMG)  
> **Curso:** Engenharia Elétrica  
> **Autor:** Arthur Vieira de Assis Moreira  
> **Orientador:** Prof. Luciano de Errico

## 📌 Sobre o Projeto

Este projeto investiga o impacto de diferentes mecanismos de Qualidade de Serviço (QoS) em Redes Definidas por Software (SDN). O estudo foca na análise comparativa entre os comportamentos dos protocolos de transporte **TCP** e **UDP** sob condições de congestionamento, utilizando técnicas de gerenciamento de filas e policiamento de tráfego.

O objetivo principal é demonstrar como a separação entre plano de controle e plano de dados (SDN) facilita a implementação de políticas de tráfego flexíveis (HTB, FQ-CODEL, Policiamento) para mitigar problemas como *bufferbloat*, perda de pacotes e *jitter* em aplicações de tempo real.

## 🛠️ Tecnologias e Ferramentas

O ambiente experimental foi construído utilizando:

* **[Mininet](http://mininet.org/):** Emulador de rede para criação das topologias virtuais.
* **[Ryu Controller](https://ryu-sdn.org/):** Controlador SDN baseado em Python para gerenciamento dos fluxos OpenFlow.
* **[Open vSwitch (OVS)](https://www.openvswitch.org/):** Switch virtual compatível com OpenFlow.
* **Linux Traffic Control (`tc`):** Ferramenta para configuração de disciplinas de enfileiramento (`htb`, `tbf`, `fq_codel`, `pfifo`).
* **[iPerf3](https://iperf.fr/):** Gerador de tráfego TCP e UDP para medição de vazão, perda e jitter.
* **LaTeX (Beamer):** Utilizado para a documentação e apresentação dos resultados.

## 🧪 Cenários de Teste

O estudo foi dividido em 5 cenários progressivos para validar as hipóteses:

1.  **Competição sem QoS (Baseline):** Estabelece a linha de base em um link congestionado (10 Mbps) com fila FIFO. Demonstra a agressividade do UDP contra a cooperatividade do TCP.
2.  **Priorização com Filas (HTB):** Proteção de um fluxo crítico (H1) contra um fluxo de baixa prioridade. Evidencia o fenômeno de *Bufferbloat*.
3.  **Múltiplos Fluxos e Inanição:** Três fluxos competindo. Demonstra o fenômeno de *Flow Starvation* (Inanição) onde um fluxo UDP é completamente silenciado devido à saturação da fila.
4.  **Policiamento (Ingresso) vs. Modelagem (Egresso):** Compara o descarte imediato na entrada (*Policer*) com o atraso e enfileiramento na saída (*Shaper*).
5.  **Rede Corporativa (FIFO vs. HTB vs. FQ-CODEL):** Simulação realista com tráfego misto (VoIP, Vídeo, Web, Bulk). Compara a eficácia das diferentes disciplinas de fila.

### Conclusões Sintetizadas
* **TCP vs UDP:** A eficácia da QoS depende fundamentalmente da interação com o protocolo.
    * Para **UDP**, a QoS atua como "punição" (descarte de pacotes).
    * Para **TCP**, a QoS atua como "sinalização" (aumento de RTT/Backpressure), levando o protocolo a se adaptar.
* **Arquiteturas:** O *Policer* é ideal para borda (limite rígido), enquanto o *Shaper* é ideal para o núcleo (compartilhamento).
* **FQ-CODEL:** Mostrou-se a solução mais robusta para redes gerais, oferecendo baixa latência e justiça sem necessidade de configuração manual de classes.

## 🚀 Como Executar
1. Instale o Mininet, Ryu e iPerf3
```bash
sudo apt-get update
sudo apt-get install mininet openvswitch-switch python3-pip iperf3
pip3 install ryu
```

2. Clonar o repositório
```bash
git clone [https://github.com/arthurvdeassis/TCC](https://github.com/arthurvdeassis/TCC)
cd TCC
```

3. Iniciar o controlador

```bash
## Para o switch básico (Cenário 1)
ryu-manager simple_switch_13.app
```

```bash
## Para os cenários com QoS (Cenários 2-5)
ryu-manager CONTROLADOR DESEJADO
```

4. Executar os experimento desejado
```bash
sudo python3 TESTE DESEJADO
```

## 📜 Licença
Este projeto foi desenvolvido para fins acadêmicos como requisito para obtenção do título de Engenheiro Eletricista. Sinta-se à vontade para utilizá-lo como referência, citando a autoria.
