import numpy as np
import torch
from torch import nn
from sklearn.metrics import accuracy_score, roc_auc_score, f1_score
from tqdm import tqdm
from transformers import RobertaTokenizer, RobertaForSequenceClassification, RobertaModel
# from utils.data import initialize_data

device = 'cuda' if torch.cuda.is_available() else 'cpu'


class DualRoberta(nn.Module):
    def __init__(self, model, tokenizer):
        super(DualRoberta, self).__init__()
        self.model = model
        # freeze roberta
        for param in self.model.parameters():
            param.requires_grad = False
        self.tokenizer = tokenizer
        self.attention = nn.MultiheadAttention(embed_dim=768, num_heads=4, dropout=0.5)
        self.attn_layernorm = nn.LayerNorm(768)
        self.linear1 = nn.Linear(768, 768)
        # nn.init.xavier_normal_(self.linear1.weight)
        self.mlp_layernorm = nn.LayerNorm(768)
        self.linear2 = nn.Linear(768, 2)
        # nn.init.xavier_normal_(self.linear2.weight)

    def forward(self, tweet_text, image_description):
        tweet_tokens = self.tokenizer(tweet_text, return_tensors="pt", padding=True)['input_ids'].to(device)
        image_description_tokens = self.tokenizer(image_description, return_tensors="pt", padding=True)['input_ids'].to(device)
        tweet_emb = torch.mean(self.model(tweet_tokens).last_hidden_state, dim=1)
        image_emb = torch.mean(self.model(image_description_tokens).last_hidden_state, dim=1)
        multimodal_emb, _attn_weights = self.attention(query=image_emb, key=tweet_emb, value=tweet_emb)
        multimodal_emb = self.attn_layernorm(multimodal_emb + tweet_emb)
        outputs = nn.functional.relu(self.linear1(multimodal_emb))
        outputs = self.mlp_layernorm(outputs + multimodal_emb)
        outputs = self.linear2(outputs)
        return outputs

def train(train_loader, valid_loader, test_loader, nepochs):
    # roberta_model = RobertaForSequenceClassification.from_pretrained('roberta-base', num_labels=2)
    roberta_model = RobertaModel.from_pretrained('roberta-base')
    roberta_tokenizer = RobertaTokenizer.from_pretrained('roberta-base')
    device = 'cuda' if torch.cuda.is_available() else 'cpu'    
    model = DualRoberta(roberta_model, roberta_tokenizer)
    model.to(device)

    print('trainable params: {0}/{1}'.format(sum(p.numel() for p in model.parameters() if p.requires_grad), sum(p.numel() for p in model.parameters())))

    criterion = nn.CrossEntropyLoss(weight=train_loader.dataset.class_weights().to(device))
    # criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=5e-1, weight_decay=0.01)
    for epoch in range(nepochs):
        model.train()
        for batch_idx, (image, tweet, label) in enumerate(train_loader):
            image = image
            tweet = tweet
            label = torch.Tensor(label).long().to(device)
            optimizer.zero_grad()
            out = model(tweet, image)
            loss = criterion(out, label)
            loss.backward()
            optimizer.step()
            # if batch_idx % 1 == 0:
                # print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                            # epoch, batch_idx * len(image), len(train_loader.dataset),
                            # 100. * batch_idx / len(train_loader), loss.item()))
        print('Epoch: {}/{}'.format(epoch, nepochs))
        test(model, valid_loader)
        print('-'*25)
    print('-'*25)
    # print('Testing...')
    # test(model, test_loader)
    # print('-'*25)
    return


def test(model, valid_loader):
    model.eval()
    test_loss = 0
    preds = []
    probs = []
    gt = []
    criterion = nn.CrossEntropyLoss(weight=valid_loader.dataset.class_weights().to(device))
    # criterion = nn.CrossEntropyLoss()
    model.eval()
    with torch.no_grad():
        for image, tweet, label in valid_loader:
            label = torch.Tensor(label).long().to(device)
            output = model(tweet, image)
            test_loss += criterion(output, label).item()
            pred = output.argmax(dim=1, keepdim=True)
            # prob = nn.functional.softmax(output, dim=1).max(dim=1, keepdim=True)[0]
            # positive class likelihood
            prob = nn.functional.softmax(output, dim=1)[:, 1]
            preds.extend(pred.cpu().numpy())
            gt.extend(label.cpu().numpy())
            probs.extend(prob.cpu().numpy())
    test_loss /= len(valid_loader.dataset)
    gt = np.array(gt).flatten()
    preds = np.array(preds).flatten()
    probs = np.array(probs).flatten()
    performance = metrics(gt, preds, probs)
    print('\nAverage loss: {:.4f}, Accuracy: {:.3f} , F1-macro: {:.3f} , F1: {:.3f}\n'.format(test_loss, performance['acc'], performance['f1_macro'], performance['f1']))
    return


def metrics(gt, preds, probs):
    acc = (gt == preds).sum() / len(gt)
    auc = roc_auc_score(gt, probs)
    f1_macro = f1_score(gt, preds, average='macro')
    f1 = f1_score(gt, preds, pos_label=1)
    return {'acc': acc, 'auc': auc, 'f1_macro': f1_macro, 'f1': f1}
