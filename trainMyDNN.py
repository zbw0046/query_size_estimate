import argparse
import time
import os

import torch
from torch.autograd import Variable
from torch.utils.data import DataLoader

from myDNN.util import *
from myDNN.data import get_torch_train_data
from myDNN.model import SetConv


def unnormalize_torch(vals, min_val, max_val):
    vals = (vals * (max_val - min_val)) + min_val
    return torch.exp(vals)


def qerror_loss(preds, targets, min_val, max_val):
    qerror = []
    preds = unnormalize_torch(preds, min_val, max_val)
    targets = unnormalize_torch(targets, min_val, max_val)

    for i in range(len(targets)):
        if (preds[i] > targets[i]).cpu().data.numpy()[0]:
            qerror.append(preds[i] / targets[i])
        else:
            qerror.append(targets[i] / preds[i])
    return torch.mean(torch.cat(qerror))


def predict(model, data_loader, cuda):
    preds = []
    t_total = 0.

    model.eval()
    for batch_idx, data_batch in enumerate(data_loader):

        predicates, selectivity, joins, targets, predicate_masks, join_masks = data_batch

        if cuda:
            predicates, selectivity, joins, targets = predicates.cuda(), selectivity.cuda(), joins.cuda(), targets.cuda()
            predicate_masks, join_masks = predicate_masks.cuda(), join_masks.cuda()
        predicates, selectivity, joins, targets = Variable(predicates), Variable(selectivity), Variable(joins), Variable(
            targets)
        predicate_masks, join_masks = Variable(predicate_masks), Variable(
            join_masks)

        t = time.time()
        outputs = model(predicates, joins, selectivity, predicate_masks, join_masks)
        t_total += time.time() - t

        for i in range(outputs.data.shape[0]):
            preds.append(outputs.data[i])

        return preds, t_total


def print_qerror(preds_unnorm, labels_unnorm):
    qerror = []
    for i in range(len(preds_unnorm)):
        if preds_unnorm[i] > float(labels_unnorm[i]):
            qerror.append(preds_unnorm[i] / float(labels_unnorm[i]))
        else:
            qerror.append(float(labels_unnorm[i]) / float(preds_unnorm[i]))

    print("Median: {}".format(np.median(qerror)))
    print("90th percentile: {}".format(np.percentile(qerror, 90)))
    print("95th percentile: {}".format(np.percentile(qerror, 95)))
    print("99th percentile: {}".format(np.percentile(qerror, 99)))
    print("Max: {}".format(np.max(qerror)))
    print("Mean: {}".format(np.mean(qerror)))


def train_and_predict(workload_name, num_queries, num_epochs, batch_size, hid_units, cuda):
    # Load training and validation data
    num_materialized_samples = 1000

    train_dataset, test_dataset, input_dim, data_loader, min_label_val, max_label_val, labels_train, labels_test = get_torch_train_data()
    # dicts, column_min_max_vals, min_val, max_val, labels_train, labels_test, max_num_joins, max_num_predicates, train_data, test_data = get_train_datasets(
    #     num_queries, num_materialized_samples)
    # table2vec, column2vec, op2vec, join2vec = dicts

    # Train model
    # sample_feats = len(table2vec) + num_materialized_samples  # dim of one sample+table
    # predicate_feats = len(column2vec) + len(op2vec) + 1  # dim of one predicate
    # join_feats = len(join2vec)  # dim of one join
    predicate_feats, selectivity_feats, join_feats = input_dim[0], input_dim[1], input_dim[2]

    # model = SetConv(sample_feats, predicate_feats, join_feats, hid_units)
    hid_units_predicate = 128
    hid_units_join_selectivity = 64
    hid_units_join = 128
    hid_units_output = 128
    model = SetConv(predicate_feats, join_feats, selectivity_feats, hid_units_predicate, hid_units_join_selectivity,
                 hid_units_join, hid_units_output)

    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

    if cuda:
        model.cuda()

    train_data_loader = DataLoader(train_dataset, batch_size=batch_size)
    test_data_loader = DataLoader(test_dataset, batch_size=batch_size)

    model.train()
    for epoch in range(num_epochs):
        loss_total = 0.

        for batch_idx, data_batch in enumerate(train_data_loader):

            predicates, selectivity, joins, targets, predicate_masks, join_masks = data_batch

            if cuda:
                predicates, selectivity, joins, targets = predicates.cuda(), selectivity.cuda(), joins.cuda(), targets.cuda()
                predicate_masks, join_masks = predicate_masks.cuda(), join_masks.cuda()
            predicates, selectivity, joins, targets = Variable(predicates), Variable(selectivity), Variable(joins), Variable(
                targets)
            predicate_masks, join_masks = Variable(predicate_masks), Variable(
                join_masks)

            optimizer.zero_grad()
            outputs = model(predicates, joins, selectivity, predicate_masks, join_masks)
            loss = qerror_loss(outputs, targets.float(), min_label_val, max_label_val)
            loss_total += loss.item()
            loss.backward()
            optimizer.step()

        print("Epoch {}, loss: {}".format(epoch, loss_total / len(train_data_loader)))
        preds_test, t_total = predict(model, test_data_loader, cuda)
        preds_test_unnorm = unnormalize_labels(preds_test, min_label_val, max_label_val)
        labels_test_unnorm = unnormalize_labels(labels_test, min_label_val, max_label_val)
        print("\nQ-Error validation set:")
        print_qerror(preds_test_unnorm, labels_test_unnorm)
        print("")

    # Get final training and validation set predictions
    preds_train, t_total = predict(model, train_data_loader, cuda)
    print("Prediction time per training sample: {}".format(t_total / len(labels_train) * 1000))

    preds_test, t_total = predict(model, test_data_loader, cuda)
    print("Prediction time per validation sample: {}".format(t_total / len(labels_test) * 1000))

    # Unnormalize
    preds_train_unnorm = unnormalize_labels(preds_train, min_label_val, max_label_val)
    labels_train_unnorm = unnormalize_labels(labels_train, min_label_val, max_label_val)

    preds_test_unnorm = unnormalize_labels(preds_test, min_label_val, max_label_val)
    labels_test_unnorm = unnormalize_labels(labels_test, min_label_val, max_label_val)

    # Print metrics
    print("\nQ-Error training set:")
    print_qerror(preds_train_unnorm, labels_train_unnorm)

    print("\nQ-Error validation set:")
    print_qerror(preds_test_unnorm, labels_test_unnorm)
    print("")
    #
    # # Load test data
    # file_name = "workloads/" + workload_name
    # joins, predicates, tables, samples, label = load_data(file_name, num_materialized_samples)
    #
    # # Get feature encoding and proper normalization
    # # samples_test = encode_samples(tables, samples, table2vec)
    # predicates_test, joins_test = encode_data(predicates, joins, column_min_max_vals, column2vec, op2vec, join2vec)
    # labels_test, _, _ = normalize_labels(label, min_val, max_val)
    #
    # print("Number of test samples: {}".format(len(labels_test)))
    #
    # max_num_predicates = max([len(p) for p in predicates_test])
    # max_num_joins = max([len(j) for j in joins_test])
    #
    # # Get test set predictions
    # test_data = make_dataset(predicates_test, joins_test, labels_test, max_num_joins, max_num_predicates)
    # test_data_loader = DataLoader(test_data, batch_size=batch_size)
    #
    # preds_test, t_total = predict(model, test_data_loader, cuda)
    # print("Prediction time per test sample: {}".format(t_total / len(labels_test) * 1000))
    #
    # # Unnormalize
    # preds_test_unnorm = unnormalize_labels(preds_test, min_val, max_val)
    #
    # # Print metrics
    # print("\nQ-Error " + workload_name + ":")
    # print_qerror(preds_test_unnorm, label)
    #
    # # Write predictions
    # file_name = "results/predictions_" + workload_name + ".csv"
    # os.makedirs(os.path.dirname(file_name), exist_ok=True)
    # with open(file_name, "w") as f:
    #     for i in range(len(preds_test_unnorm)):
    #         f.write(str(preds_test_unnorm[i]) + "," + label[i] + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testset", help="synthetic, scale, or job-light", type=str, default="synthetic")
    parser.add_argument("--queries", help="number of training queries (default: 10000)", type=int, default=40000)
    parser.add_argument("--epochs", help="number of epochs (default: 10)", type=int, default=3000)
    parser.add_argument("--batch", help="batch size (default: 1024)", type=int, default=4096)
    parser.add_argument("--hid", help="number of hidden units (default: 256)", type=int, default=256)
    parser.add_argument("--cuda", help="use CUDA", action="store_true")
    args = parser.parse_args()
    args.cuda = True
    train_and_predict(args.testset, args.queries, args.epochs, args.batch, args.hid, args.cuda)



if __name__ == "__main__":
    main()
